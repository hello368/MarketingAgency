"""
MARTS Phase 5 — Task Tracker (@best 2.0)

Trigger format (space-separated):
    @best @Assignee1 @Assignee2  Client  City  Task description  [https://...]

Lifecycle
---------
New task    : @best mentioned + ≥1 human assignee
              → Sheet row created, per-assignee nag timers started in SQLite
Acknowledge : any assignee sends ANY message in the same thread
              → their nag timer cancelled
Complete    : "done" / "completed" / "finished" (negation-aware) in thread
              → Sheet batch-updated, all nag timers closed

Persistent Nag Escalation (per assignee)
------------------------------------------
  L1 (15 m / NAG_L1_SECONDS): polite reminder
  L2 (30 m / NAG_L2_SECONDS): urgent warning
  L3 (45 m / NAG_L3_SECONDS): final escalation → Google Chat + Michael Telegram

All user-facing strings sent to Google Chat are 100% Business English.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

import db
import telegram_bot
import gchat_sender
from config import NAG_L1_SECONDS, NAG_L2_SECONDS, NAG_L3_SECONDS

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)
TZ  = ZoneInfo("America/Los_Angeles")  # PST/PDT — San Francisco

# ── Configuration ─────────────────────────────────────────────────────────────
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "best")
_CREDS_PATH      = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json")
_SPREADSHEET_ID  = os.getenv("SPREADSHEET_ID", "")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

TAB_TASKS     = "Task_Tracker"
_TASK_HEADERS = [
    "Date", "Thread ID", "Client", "City",
    "Task Description", "Assignee(s)", "Status", "Requested At",
    "Completed At",
    # Columns J onwards are dynamically populated — one URL per cell, no limit
]

# Fixed column letters (A–I are always stable)
_COL_STATUS       = "G"   # 7
_COL_COMPLETED_AT = "I"   # 9
_URL_START_COL    = 9     # 0-based index of column J


def _col_letter(zero_based_index: int) -> str:
    """Convert a 0-based column index to a spreadsheet letter (e.g. 9 → 'J', 35 → 'AJ')."""
    result = ""
    n = zero_based_index + 1  # 1-based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result

# Positive completion keywords (English only — no Korean in source strings)
_DONE_KEYWORDS = frozenset(["done", "completed", "finished", "complete", "closed"])
# Negation context window: 20 chars before keyword
_NEGATIONS     = ("not ", "haven't ", "isn't ", "wasn't ", "no ", "never ")

# Nag alert templates (Business English — these are sent to Google Chat)
_NAG_L1 = (
    "📋 *Task Acknowledgment Required*\n"
    "Hi *{name}*, this task has been open for 15 minutes without a response. "
    "Please acknowledge to confirm you have received it and meet our SLA.\n"
    "— MARTS Tracker"
)
_NAG_L2 = (
    "⚠️ *[Urgent] Task Overdue — 30 Minutes Elapsed*\n"
    "*{name}*, 30 minutes have passed without any acknowledgment or update on this task. "
    "Please respond immediately to avoid further escalation.\n"
    "— MARTS Tracker"
)
_NAG_L3 = (
    "🚨 *[Final Escalation] Task Unacknowledged — 45 Minutes*\n"
    "*{name}* has not responded after 45 minutes. "
    "*Michael* — manual intervention is required on this task.\n"
    "— MARTS Tracker"
)
_NAG_L3_TELEGRAM = (
    "🚨 [FINAL ESCALATION]\n"
    "{name} is still unresponsive after 45 min on a task in '{space}'.\n"
    "Thread: {thread}\n"
    "Manual intervention required."
)

# ── Shared state ──────────────────────────────────────────────────────────────
_spreadsheet: gspread.Spreadsheet | None = None
_sheet_lock   = threading.Lock()


# ── Sheet helpers ─────────────────────────────────────────────────────────────

def _get_worksheet() -> gspread.Worksheet:
    global _spreadsheet
    with _sheet_lock:
        if _spreadsheet is None:
            creds = Credentials.from_service_account_file(_CREDS_PATH, scopes=_SCOPES)
            client = gspread.authorize(creds)
            _spreadsheet = client.open_by_key(_SPREADSHEET_ID)

        try:
            ws = _spreadsheet.worksheet(TAB_TASKS)
        except gspread.WorksheetNotFound:
            ws = _spreadsheet.add_worksheet(
                title=TAB_TASKS, rows=2000, cols=50  # 50 cols = A–AX, plenty for dynamic URL columns
            )
            ws.append_row(_TASK_HEADERS, value_input_option="USER_ENTERED")
            ws.format(
                "1:1",
                {
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    },
                    "backgroundColor": {"red": 0.07, "green": 0.45, "blue": 0.24},
                },
            )
            log.info("[TaskTracker] Created tab: %s", TAB_TASKS)
        return ws


def _find_task_row(ws: gspread.Worksheet, thread_id: str) -> int | None:
    """One API call; safe for thread IDs containing '/' characters."""
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if len(row) > 1 and row[1] == thread_id:
            return i
    return None


# ── Text parsing helpers ──────────────────────────────────────────────────────

def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s]+", text)


def _parse_attachments(attachments: list[dict]) -> list[str]:
    """
    Extract clean, clickable URLs from Google Chat attachment objects.
    Returns plain URLs only — no filenames or parentheses.

    Priority per attachment:
      1. driveDataRef.driveFileId  → permanent Drive view URL
      2. downloadUri               → direct download link (uploaded files)
      3. thumbnailUri              → image preview (fallback)
    """
    urls = []
    for i, att in enumerate(attachments):
        log.info("[TaskTracker] attachment[%d] full object: %s", i, att)

        drive_id     = att.get("driveDataRef", {}).get("driveFileId", "")
        download_uri = att.get("downloadUri", "")
        thumbnail    = att.get("thumbnailUri", "")

        if drive_id:
            url = f"https://drive.google.com/file/d/{drive_id}/view"
            urls.append(url)
            log.info("[TaskTracker] attachment[%d] → Drive: %s", i, url)
        elif download_uri:
            urls.append(download_uri)
            log.info("[TaskTracker] attachment[%d] → download: %s", i, download_uri[:120])
        elif thumbnail:
            urls.append(thumbnail)
            log.info("[TaskTracker] attachment[%d] → thumbnail: %s", i, thumbnail[:120])
        else:
            log.warning("[TaskTracker] attachment[%d] → no URL found. keys=%s", i, sorted(att.keys()))

    log.info("[TaskTracker] _parse_attachments → %d URL(s): %s", len(urls), urls)
    return urls


_SKIP_SYMBOLS = frozenset({"-", "."})


def _strip_all_mentions(text: str, all_display_names: list[str]) -> str:
    """
    Remove every mention form from text before content parsing.

    Handles three formats Google Chat uses:
      1. <users/123456789>      — bracket ID mention
      2. @FirstName LastName    — @ token where full name follows (split by space)
      3. @FirstName             — @ token with only first name (surname already gone
                                  or single-word name)

    Strategy for multi-word names (e.g. "Michael Kay"):
      Build pattern  @Michael(?:\\s+Kay)?  so both "@Michael Kay" and a
      lone "@Michael" are consumed, leaving no orphan surname.
    """
    # Pass 1: bracket mentions  <users/...>
    text = re.sub(r"<users/[^>]+>", "", text)

    # Pass 2: @ + display name (handles split surnames)
    for name in all_display_names:
        if not name:
            continue
        words = name.split()
        if len(words) >= 2:
            first   = re.escape(words[0])
            rest    = r"\s+".join(re.escape(w) for w in words[1:])
            pattern = r"@" + first + r"(?:\s+" + rest + r")?"
        else:
            pattern = r"@" + re.escape(words[0])
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Pass 3: any remaining @token (catches @best or unknown names)
    text = re.sub(r"@\S+", "", text)

    return re.sub(r"\s+", " ", text).strip()


def _parse_task_text(text: str, mentions: list[dict]) -> dict:
    """
    Parse format: @best @Assignee1 @Assignee2 [Client] [City] Description [URLs]

    Client / City resolution rules (evaluated in order):

    Rule 1 — Short string fallback:
        Fewer than 3 non-mention, non-URL words present
        → Client = 'General', City = 'General'
        → Full cleaned text (skip symbols stripped) = Description

    Rule 2 — Explicit placeholder:
        word[0] or word[1] is '-' or '.'
        → that slot = 'General' (placeholder stripped from Description)

    Rule 3 — Normal input:
        word[0] = Client, word[1] = City, word[2:] = Description

    In all cases skip symbols ('-', '.') are never written to Description.
    """
    assignees = [
        m["display_name"]
        for m in mentions
        if m["display_name"].lower() != BOT_DISPLAY_NAME.lower()
    ]

    # Strip ALL mention forms before parsing — includes bot + every assignee name
    all_names = [m["display_name"] for m in mentions]
    clean     = _strip_all_mentions(text, all_names)
    urls          = _extract_urls(clean)
    clean_no_urls = re.sub(r"https?://[^\s]+", "", clean).strip()
    words         = clean_no_urls.split()

    # ── Rule 1: fewer than 3 words → no room for client + city + description ──
    if len(words) < 3:
        desc_words = [w for w in words if w not in _SKIP_SYMBOLS]
        return {
            "assignees":   assignees,
            "client":      "General",
            "city":        "General",
            "description": " ".join(desc_words) if desc_words else "—",
            "assets":      ", ".join(urls),
        }

    # ── Rules 2 & 3: parse with skip-symbol awareness ─────────────────────────
    client = "General" if words[0] in _SKIP_SYMBOLS else words[0]
    city   = "General" if words[1] in _SKIP_SYMBOLS else words[1]

    # Strip skip-symbol placeholders from the description tail
    desc_words = [w for w in words[2:] if w not in _SKIP_SYMBOLS]
    description = " ".join(desc_words) or "—"

    return {
        "assignees":   assignees,
        "client":      client,
        "city":        city,
        "description": description,
        "assets":      ", ".join(urls),
    }


def _is_completion(text: str) -> bool:
    """Negation-aware completion detection (English keywords only)."""
    lower = text.lower()
    for kw in _DONE_KEYWORDS:
        idx = lower.find(kw)
        if idx == -1:
            continue
        context = lower[max(0, idx - 20): idx]
        if any(neg in context for neg in _NEGATIONS):
            log.debug("[TaskTracker] Negation guard: '%s' in '%s'", kw, text[:60])
            continue
        return True
    return False


def _bot_was_mentioned(text: str) -> bool:
    return f"@{BOT_DISPLAY_NAME}".lower() in text.lower()


def _extract_mentions_from_message(message: dict) -> list[dict]:
    """Extract human @mentions from a raw Google Chat message dict."""
    mentions = []
    for annotation in message.get("annotations", []):
        if annotation.get("type") == "USER_MENTION":
            user    = annotation.get("userMention", {}).get("user", {})
            display = user.get("displayName", "")
            if display and user.get("type") != "BOT":
                mentions.append({"display_name": display})
    return mentions


# ── Primary entry point (called from main.py) ────────────────────────────────

def _collect_attachments(event: dict, message: dict) -> list[dict]:
    """
    Google Chat delivers attachment data under several different keys
    depending on the integration type (Chat App vs Workspace Add-on).
    Try every known location so nothing is silently dropped.
    """
    candidates = (
        message.get("attachment")                                        # Chat App (singular)
        or message.get("attachments")                                    # legacy / some Add-ons
        or event.get("attachment")                                       # top-level (rare)
        or event.get("attachments")                                      # top-level plural
        or event.get("chat", {}).get("messagePayload", {})
                   .get("message", {}).get("attachment")                 # raw Add-on path
        or []
    )
    if candidates:
        log.info("[TaskTracker] attachments found (%d items) — keys: %s",
                 len(candidates),
                 [sorted(a.keys()) for a in candidates[:3]])
    else:
        log.info("[TaskTracker] NO attachments found — event keys=%s | message keys=%s",
                 sorted(event.keys()), sorted(message.keys()))
    return candidates


def process_task_message(event: dict) -> dict | None:
    """
    Primary entry point for main.py's _handle_message().

    Builds combined_assets (text URLs + attachment URLs) here so the
    combination is explicit and testable before being passed to handle_task_event.
    """
    message     = event.get("message", {})
    sender      = message.get("sender", {})
    space       = message.get("space", {})
    thread      = message.get("thread", {})

    sender_type = sender.get("type", "HUMAN")
    if sender_type == "BOT":
        return None

    text            = message.get("text", "").strip()
    raw_attachments = _collect_attachments(event, message)

    # ── Build ordered URL list: text links first, then attachment links ───────
    text_urls   = _extract_urls(text)
    attach_urls = _parse_attachments(raw_attachments)
    url_list    = text_urls + attach_urls

    log.info(
        "[TaskTracker] URL list — text=%d %s | attach=%d %s | total=%d",
        len(text_urls), text_urls[:3],
        len(attach_urls), attach_urls[:3],
        len(url_list),
    )

    return handle_task_event(
        text       = text,
        thread_id  = thread.get("name", ""),
        space_name = space.get("name", ""),
        sender_name= sender.get("displayName", ""),
        mentions   = _extract_mentions_from_message(message),
        attachments= raw_attachments,
        url_list   = url_list,
        now        = datetime.now(TZ),
    )


# ── Nag timer helpers ─────────────────────────────────────────────────────────

def create_nag_timers(
    thread_id: str, space_name: str, assignees: list[str], now: datetime
) -> None:
    """Create one nag timer row per assignee in SQLite."""
    l1 = (now + timedelta(seconds=NAG_L1_SECONDS)).isoformat()
    l2 = (now + timedelta(seconds=NAG_L2_SECONDS)).isoformat()
    l3 = (now + timedelta(seconds=NAG_L3_SECONDS)).isoformat()
    for assignee in assignees:
        db.create_task_nag_timer(thread_id, space_name, assignee, l1, l2, l3)
        log.info(
            "[TaskTracker] Nag timer created — assignee=%s L1=%s L2=%s L3=%s",
            assignee, l1, l2, l3,
        )


def acknowledge_nag_timers(thread_id: str, sender_name: str) -> None:
    """Cancel nag timers when the assignee sends ANY message in the task thread."""
    count = db.acknowledge_task_nag_timers(thread_id, sender_name)
    if count:
        log.info("[TaskTracker] %d nag timer(s) acknowledged — %s in %s", count, sender_name, thread_id)


def close_nag_timers(thread_id: str) -> None:
    """Mark all nag timers for a thread as closed (task completed)."""
    db.close_task_nag_timers(thread_id)
    log.info("[TaskTracker] All nag timers closed for thread: %s", thread_id)


def check_and_fire_nag_alerts() -> None:
    """
    Called by APScheduler every 15 seconds.
    Fires the next escalation level for any expired, un-acknowledged nag timers.
    Level 3 also sends a Telegram alert to Michael.
    """
    now_iso = datetime.now(TZ).isoformat()

    for timer in db.get_expired_nag_timers(now_iso):
        assignee   = timer["assignee"]
        space_name = timer["space_name"]
        thread_id  = timer["thread_id"]
        level      = timer["nag_level"]  # next level to fire
        timer_id   = timer["id"]

        if level == 1:
            text = _NAG_L1.format(name=assignee)
        elif level == 2:
            text = _NAG_L2.format(name=assignee)
        else:
            text = _NAG_L3.format(name=assignee)
            # Escalate to Michael via Telegram as well
            tg_text = _NAG_L3_TELEGRAM.format(
                name=assignee, space=space_name, thread=thread_id
            )
            telegram_bot.send_alert("Michael", tg_text)

        try:
            gchat_sender.reply_to_thread(space_name, thread_id, text)
            db.mark_nag_level_sent(timer_id, level)
            log.info(
                "[TaskTracker] Nag L%d fired — assignee=%s thread=%s",
                level, assignee, thread_id,
            )
        except Exception as e:
            log.error("[TaskTracker] Nag alert failed (L%d, %s): %s", level, assignee, e)


# ── Public API ────────────────────────────────────────────────────────────────

def handle_task_event(
    *,
    text: str,
    thread_id: str,
    space_name: str,
    sender_name: str,
    mentions: list[dict],
    attachments: list[dict],
    url_list: list[str],
    now: datetime,
) -> dict | None:
    """
    Process a Google Chat message for task lifecycle events.

    Returns dict {"text": <Business English bot reply>} or None.
    """
    if not _SPREADSHEET_ID:
        log.warning("[TaskTracker] SPREADSHEET_ID not set — skipping")
        return None

    now_str  = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")

    try:
        ws = _get_worksheet()
    except Exception as e:
        log.error("[TaskTracker] Sheets connection failed: %s", e)
        return None

    # ── Scenario A: Task Completion ───────────────────────────────────────────
    if _is_completion(text):
        row_idx = _find_task_row(ws, thread_id)

        log.info("[TaskTracker] Scenario A — closing url_list=%s", url_list)

        if row_idx is None:
            log.info("[TaskTracker] Completion signal — no open task for thread %s", thread_id)
            return {
                "text": (
                    f"✅ *{sender_name}* marked this as complete.\n"
                    "_(No open task was found for this thread — sheet not updated.)_"
                )
            }

        try:
            # Find the first empty column in this row from J onwards,
            # so closing URLs don't overwrite existing reference links.
            row_data   = ws.row_values(row_idx)
            start_col  = _URL_START_COL
            while start_col < len(row_data) and row_data[start_col]:
                start_col += 1

            updates = [
                {"range": f"{_COL_STATUS}{row_idx}",       "values": [["✅ Completed"]]},
                {"range": f"{_COL_COMPLETED_AT}{row_idx}", "values": [[now_str]]},
            ]
            for i, url in enumerate(url_list):
                updates.append({
                    "range":  f"{_col_letter(start_col + i)}{row_idx}",
                    "values": [[url]],
                })

            ws.batch_update(updates, value_input_option="USER_ENTERED")
            log.info(
                "[TaskTracker] Task closed — row=%d thread=%s by=%s closing_urls=%d",
                row_idx, thread_id, sender_name, len(url_list),
            )
        except Exception as e:
            log.error("[TaskTracker] batch_update failed (row=%d): %s", row_idx, e)
            return None

        close_nag_timers(thread_id)

        assets_preview = "\n".join(url_list) if url_list else "—"
        return {
            "text": (
                f"✅ *Task Completed!*\n"
                f"👤 Closed by: *{sender_name}*\n"
                f"🕐 Completed at: {now_str}\n"
                f"📎 Final assets ({len(url_list)}):\n{assets_preview}"
            )
        }

    # ── Scenario B: New Task Registration ─────────────────────────────────────
    if not _bot_was_mentioned(text):
        return None

    parsed    = _parse_task_text(text, mentions)
    assignees = parsed["assignees"]

    if not assignees:
        log.debug("[TaskTracker] @best triggered but no assignees found")
        return None

    assignee_str = ", ".join(assignees)

    log.info(
        "[TaskTracker] Scenario B — %d URL(s) → cols J+ : %s",
        len(url_list), url_list,
    )

    # Fixed cols A–I, then one URL per cell from J onwards (no limit)
    new_row = [
        date_str, thread_id,
        parsed["client"], parsed["city"], parsed["description"],
        assignee_str,
        "🏃 In Progress", now_str,
        "",          # Completed At (col I)
        *url_list,   # cols J, K, L, … — one clean URL each
    ]

    try:
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        log.info(
            "[TaskTracker] Task created — client=%s city=%s assignees=%s urls=%d thread=%s",
            parsed["client"], parsed["city"], assignee_str, len(url_list), thread_id,
        )
    except Exception as e:
        log.error("[TaskTracker] append_row failed: %s", e)
        return None

    create_nag_timers(thread_id, space_name, assignees, now)

    desc_preview  = parsed["description"][:100] + ("…" if len(parsed["description"]) > 100 else "")
    assets_preview = "\n".join(url_list) if url_list else "—"
    return {
        "text": (
            f"📝 *Task registered under [{parsed['client']}] / [{parsed['city']}]*\n"
            f"👤 Assigned to: *{assignee_str}*\n"
            f"📋 _{desc_preview}_\n"
            f"📎 {len(url_list)} link(s) archived:\n{assets_preview}\n"
            f"⏱️ SLA timer started: {now_str}\n\n"
            "_Reply *done* or *completed* in this thread to close the task._"
        )
    }
