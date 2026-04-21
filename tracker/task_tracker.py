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
import state
import telegram_bot
import gchat_sender
from config import NAG_L1_SECONDS, NAG_L2_SECONDS, NAG_L3_SECONDS

FOCUS_SECONDS          = 45 * 60   # 2700 — duration of a focus window
FOCUS_NO_REPLY_SECONDS = 10 * 60   # 600  — wait after check before resuming nags

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
    "Date", "Space", "Client", "City",
    "Task Description", "Assignee(s)", "Status", "Requested At",
    "Completed At", "Duration (min)", "Visual Progress",
    # Columns L onwards are dynamically populated — one URL per cell, no limit
]

# Matches the thread key embedded in MARTS-generated HYPERLINK formulas in col B:
#   =HYPERLINK("https://mail.google.com/chat/u/0/#chat/space/SPACE_ID/THREAD_KEY", "...")
_HYPERLINK_THREAD_RE = re.compile(r'#chat/space/[^/"]+/([^/"]+)', re.IGNORECASE)

# Fallback column letters if headers are not found in the sheet
_COL_STATUS_DEFAULT       = "G"
_COL_COMPLETED_AT_DEFAULT = "I"
_URL_START_COL            = 11    # 0-based index of column L (J=Duration, K=Visual Progress)

# Set to True after the one-time col B header + retroactive HYPERLINK migration runs
_header_migrated = False


def _col_letter(zero_based_index: int) -> str:
    """Convert a 0-based column index to a spreadsheet letter (e.g. 9 → 'J', 35 → 'AJ')."""
    result = ""
    n = zero_based_index + 1  # 1-based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _col_index(letter: str) -> int:
    """Convert column letter(s) to 1-based integer index (e.g. 'G' → 7, 'AJ' → 36)."""
    result = 0
    for c in letter.upper():
        result = result * 26 + (ord(c) - ord('A') + 1)
    return result

# Positive completion keywords (English only — no Korean in source strings)
_DONE_KEYWORDS = frozenset(["done", "completed", "finished", "complete", "closed"])
# Negation context window: 20 chars before keyword
_NEGATIONS     = ("not ", "haven't ", "isn't ", "wasn't ", "no ", "never ")

# Acknowledgment keywords — trigger Focus Mode (cancel L1/L2 nags, start 45-min timer)
_ACK_PATTERNS = [
    re.compile(r"\bok\b",            re.IGNORECASE),
    re.compile(r"\bgot\s+it\b",      re.IGNORECASE),
    re.compile(r"\bprocessing\s+now\b", re.IGNORECASE),
    re.compile(r"\bokey\b",          re.IGNORECASE),
]

# Snooze keywords — reset the 45-min focus timer
_SNOOZE_PATTERNS = [
    re.compile(r"\bstill\s+working\b", re.IGNORECASE),
    re.compile(r"\bdoing\s+it\b",      re.IGNORECASE),
]

# Focus mode message templates
_FOCUS_MODE_CONFIRMED = (
    "✅ Got it, {mention}! Status updated to *🏃 Processing*.\n"
    "I'll check back in *45 minutes*.\n"
    "Reply *done* when complete, or *still working* to extend your focus time."
)
_FOCUS_CHECK = (
    "⏰ *45-Minute Focus Check*\n"
    "{mention} — Is the task still in progress?\n"
    "📌 Task: [{client} / {city}] - {description}\n\n"
    "Reply *done* to close the task, or *still working* to continue."
)
_FOCUS_SNOOZED = (
    "🔄 Focus timer reset for another 45 minutes, {mention}. Keep going! 💪"
)
_FOCUS_NOREPLY_RESUME = (
    "⚠️ *[Focus Timeout]* No response received for 10 minutes.\n"
    "{mention} — Resuming SLA nag alerts for:\n"
    "📌 Task: [{client} / {city}] - {description}"
)

# Nag alert templates (Business English — these are sent to Google Chat)
# {mention} = <users/USER_ID> when google_chat_id is known, else display name
_NAG_L1 = (
    "Hey {mention}, ⚠️ ACTION REQUIRED!\n"
    "The following task has been open for 15 minutes without acknowledgment:\n"
    "📌 Task: [{client} / {city}] - {description}"
)
_NAG_L2 = (
    "Hey {mention}, ⚠️ ACTION REQUIRED!\n"
    "The following task has been open for 30 minutes without acknowledgment:\n"
    "📌 Task: [{client} / {city}] - {description}"
)
_NAG_L2_TELEGRAM = (
    "⚠️ [Urgent] {client} / {city} - {description} is overdue!\n"
    "Hi {name}, this task has been open for 30 minutes with no response. "
    "Please reply immediately in the Google Chat thread."
)
_NAG_L3 = (
    "🚨 *[Final Escalation] Task Unacknowledged — 45 Minutes*\n"
    "{mention} has not responded after 45 minutes.\n"
    "📌 Task: *[{client} / {city}]* - _{description}_\n"
    "*Michael* — manual intervention is required.\n"
    "— MARTS Tracker"
)
_NAG_L3_TELEGRAM = (
    "🚨 [FINAL ESCALATION]\n"
    "{name} is still unresponsive after 45 min.\n"
    "Task: {client} / {city} - {description}\n"
    "Space: {space}\n"
    "Thread: {thread}\n"
    "Manual intervention required."
)

# ── Shared state ──────────────────────────────────────────────────────────────
_spreadsheet: gspread.Spreadsheet | None = None
_sheet_lock   = threading.Lock()


# ── Sheet helpers ─────────────────────────────────────────────────────────────

def _get_worksheet() -> gspread.Worksheet:
    global _spreadsheet, _header_migrated
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
            _header_migrated = True  # Fresh sheet — no migration needed

        if not _header_migrated:
            _migrate_thread_id_to_space(ws)
            _header_migrated = True

        return ws


def _migrate_thread_id_to_space(ws: gspread.Worksheet) -> None:
    """
    One-time migration (runs once per server startup):
      1. Rename col B header from "Thread ID" → "Space".
      2. Convert existing raw thread-ID values to HYPERLINK formulas
         using "🔗 View Thread" as display text (space name unknown for old rows).
    """
    try:
        existing_headers = ws.row_values(1)
        if len(existing_headers) < 2 or existing_headers[1] != "Thread ID":
            return  # Already migrated or different layout

        ws.update_cell(1, 2, "Space")
        log.info("[TaskTracker] Migrated col B header: 'Thread ID' → 'Space'")

        all_rows = ws.get_all_values()
        updates: list[dict] = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) < 2 or not row[1]:
                continue
            cell = row[1].strip()
            if cell.upper().startswith("="):
                continue  # Already a formula
            # Expect: "spaces/SPACE_ID/threads/THREAD_KEY"
            parts = cell.split("/")
            if len(parts) >= 4 and parts[0] == "spaces" and parts[2] == "threads":
                space_id_raw   = parts[1]
                thread_key_raw = parts[3]
                formula = (
                    f'=HYPERLINK("https://mail.google.com/chat/u/0/'
                    f'#chat/space/{space_id_raw}/{thread_key_raw}", "🔗 View Thread")'
                )
                updates.append({"range": f"B{i}", "values": [[formula]]})

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            log.info(
                "[TaskTracker] Retroactively converted %d thread ID(s) to HYPERLINK formulas",
                len(updates),
            )
    except Exception as e:
        log.error("[TaskTracker] _migrate_thread_id_to_space failed: %s", e)


def _find_header_cols(ws: gspread.Worksheet) -> tuple[str, str]:
    """
    Scan row 1 and return (status_col_letter, completed_at_col_letter).
    Falls back to hardcoded defaults if headers are not found.
    """
    headers = ws.row_values(1)
    status_col = completed_col = None
    for i, h in enumerate(headers):
        h_norm = h.strip().lower()
        if h_norm == "status":
            status_col = _col_letter(i)
        elif h_norm in ("completed at", "completedat"):
            completed_col = _col_letter(i)
    status_col   = status_col   or _COL_STATUS_DEFAULT
    completed_col = completed_col or _COL_COMPLETED_AT_DEFAULT
    log.info("[TaskTracker] Header scan → status=%s completed_at=%s", status_col, completed_col)
    return status_col, completed_col


def _normalize_thread_id(tid: str) -> str:
    """Normalize a thread path to a canonical, comparable form.

    Strips whitespace, collapses slashes, removes query/fragment params, and
    strips the leading 'spaces/<id>/' segment so that both
    'spaces/ABC/threads/XYZ' and 'threads/XYZ' reduce to 'threads/XYZ' —
    making exact-match (Strategy 1) work for the most common mismatch case.
    """
    tid = tid.strip()
    tid = re.split(r"[?#]", tid)[0]            # drop ?key=val or #fragment
    tid = re.sub(r"\s+", "", tid)              # remove any embedded whitespace
    tid = re.sub(r"/+", "/", tid)              # collapse consecutive slashes
    tid = tid.strip("/")
    # Strip 'spaces/<id>/' prefix — normalises full path to threads/<key>
    tid = re.sub(r"^spaces/[^/]+/", "", tid)
    return tid


def _thread_segment(tid: str) -> str:
    """Extract the 'threads/XXXX' tail segment from a full thread path, or ''."""
    m = re.search(r"threads/[^/]+$", tid)
    return m.group(0) if m else ""


def _thread_key(tid: str) -> str:
    """Return the final path component — the bare thread key (e.g. 'XXXX' from '.../threads/XXXX')."""
    norm = _normalize_thread_id(tid)
    return norm.rsplit("/", 1)[-1] if "/" in norm else norm


def _extract_lookup_id(cell_val: str) -> str:
    """
    Normalise the col-B cell value to a comparable thread-ID string.

    New rows store a HYPERLINK formula:
        =HYPERLINK("https://mail.google.com/chat/u/0/#chat/space/SPACE_ID/THREAD_KEY", "Space Name")
    This function extracts THREAD_KEY and returns "threads/THREAD_KEY" so the
    existing normalisation strategies can match it against incoming thread paths.

    Legacy rows store a raw thread path ("spaces/X/threads/Y") — returned as-is.
    """
    stripped = cell_val.strip()
    if stripped.upper().startswith("=HYPERLINK"):
        m = _HYPERLINK_THREAD_RE.search(stripped)
        if m:
            return "threads/" + m.group(1)
    return cell_val


def _find_task_row(
    ws: gspread.Worksheet,
    thread_id: str,
    sender_name: str = "",
) -> int | None:
    """
    Find the sheet row whose Thread ID column (B) matches thread_id.

    Matching order (first hit wins):
      1. Exact match after normalizing whitespace, slashes, query params
      2. Suffix match — handles 'spaces/X/threads/Y' vs 'threads/Y'
      3. Thread-segment match — 'threads/Y' extracted from both sides
      4. Final-key match — bare thread key (last path component) compared
      5. Assignee contains-match (fallback) — any of strategies 1-4 failed;
         sender_name is found within the Assignee(s) cell (handles multi-assignee
         rows like 'Tiffany Lear, Kaye Hi' when thread IDs diverge in format).
         Thread ID wins — no assignee check is applied for strategies 1-4.
    """
    print(f"[FIND_ROW] ▶ incoming={thread_id!r} | sender={sender_name!r}", flush=True)

    if not thread_id:
        log.warning("[TaskTracker] _find_task_row called with empty thread_id")
        return None

    norm_in  = _normalize_thread_id(thread_id)
    seg_in   = _thread_segment(norm_in)
    key_in   = _thread_key(norm_in)

    # Use FORMULA render so col B HYPERLINK formulas are returned as raw strings,
    # letting _extract_lookup_id parse the embedded thread key from the URL.
    all_rows = ws.get_all_values(value_render_option='FORMULA')
    log.info(
        "[TaskTracker] Searching %d data rows — "
        "incoming_thread_norm=%s seg=%s key=%s",
        len(all_rows) - 1, norm_in, seg_in, key_in,
    )
    stored_ids = [_extract_lookup_id(r[1]) for r in all_rows[1:] if len(r) > 1 and r[1]]
    print(f"[FIND_ROW] Sheet has {len(stored_ids)} thread ID(s): {stored_ids[:5]}", flush=True)

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) < 2 or not row[1]:
            continue
        norm_stored = _normalize_thread_id(_extract_lookup_id(row[1]))

        # Strategy 1: exact — thread-first, no assignee check needed
        if norm_stored == norm_in:
            log.info("[TaskTracker] Thread match (exact) row=%d stored_norm=%s", i, norm_stored)
            return i

        # Strategy 2: suffix
        if norm_in.endswith(norm_stored) or norm_stored.endswith(norm_in):
            log.info(
                "[TaskTracker] Thread match (suffix) row=%d "
                "stored_norm=%s incoming_norm=%s",
                i, norm_stored, norm_in,
            )
            return i

        # Strategy 3: threads/XXXX segment
        if seg_in:
            seg_stored = _thread_segment(norm_stored)
            if seg_stored and seg_stored == seg_in:
                log.info(
                    "[TaskTracker] Thread match (segment) row=%d "
                    "stored_norm=%s incoming_norm=%s",
                    i, norm_stored, norm_in,
                )
                return i

        # Strategy 4: bare thread key (last path component)
        if key_in:
            key_stored = _thread_key(norm_stored)
            if key_stored and key_stored == key_in:
                log.info(
                    "[TaskTracker] Thread match (key) row=%d "
                    "stored_norm=%s key=%s",
                    i, norm_stored, key_in,
                )
                return i

    # Strategies 1-4 exhausted — log stored IDs vs incoming for diff diagnosis
    stored_norms = [
        _normalize_thread_id(_extract_lookup_id(r[1])) for r in all_rows[1:] if len(r) > 1 and r[1]
    ]
    log.warning(
        "[TaskTracker] Thread ID strategies 1-4 failed — "
        "incoming_norm=%s | stored_norms_sample=%s | rows_checked=%d",
        norm_in, stored_norms[:5], len(all_rows) - 1,
    )
    print(
        f"[FIND_ROW] S1-4 failed — norm_in={norm_in!r} | key_in={key_in!r} | "
        f"stored_norms={stored_norms[:5]}",
        flush=True,
    )

    # Strategy 5: Assignee partial match (fallback when thread format diverges)
    if sender_name:
        sender_lower = sender_name.strip().lower()
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) < 6 or not row[5]:
                continue
            assignees_cell = row[5].lower()
            if sender_lower in assignees_cell:
                norm_stored = _normalize_thread_id(row[1]) if len(row) > 1 else ""
                log.info(
                    "[TaskTracker] Thread match (assignee-contains) row=%d "
                    "assignees=%r sender=%r stored_norm=%s incoming_norm=%s",
                    i, row[5], sender_name, norm_stored, norm_in,
                )
                print(f"[FIND_ROW] ✅ S5 assignee match — row={i}", flush=True)
                return i
        log.warning(
            "[TaskTracker] Strategy 5 also failed — sender=%r not found in any assignee cell",
            sender_name,
        )

    # Strategy 6: raw substring / bare-key match without normalization
    # Catches edge cases where normalization strips the wrong segment.
    raw_key_in = thread_id.rsplit("/", 1)[-1] if "/" in thread_id else thread_id
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) < 2 or not row[1]:
            continue
        raw_stored = _extract_lookup_id(row[1])
        raw_key_stored = raw_stored.rsplit("/", 1)[-1] if "/" in raw_stored else raw_stored
        if raw_key_in and raw_key_in == raw_key_stored:
            log.info("[TaskTracker] Thread match (S6-raw-key) row=%d raw_key=%s", i, raw_key_in)
            print(f"[FIND_ROW] ✅ S6 raw-key match — row={i} key={raw_key_in!r}", flush=True)
            return i
        if raw_stored and (thread_id in raw_stored or raw_stored in thread_id):
            log.info("[TaskTracker] Thread match (S6-substring) row=%d stored=%r", i, raw_stored)
            print(f"[FIND_ROW] ✅ S6 substring match — row={i} stored={raw_stored!r}", flush=True)
            return i

    all_raw = [_extract_lookup_id(r[1]) for r in all_rows[1:] if len(r) > 1 and r[1]]
    print(
        f"[FIND_ROW] ❌ ALL 6 strategies failed — incoming={thread_id!r} | "
        f"all stored IDs: {all_raw}",
        flush=True,
    )
    log.warning(
        "[TaskTracker] No row found — thread_id=%s | norm=%s | seg=%s | key=%s | rows_checked=%d",
        thread_id, norm_in, seg_in, key_in, len(all_rows) - 1,
    )
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
        {"name": m["display_name"], "user_id": m.get("user_id", "")}
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


def is_acknowledgment(text: str) -> bool:
    """True if text contains an ACK keyword that should trigger Focus Mode."""
    return any(p.search(text) for p in _ACK_PATTERNS)


def is_snooze(text: str) -> bool:
    """True if text contains a snooze keyword ('still working', 'doing it')."""
    return any(p.search(text) for p in _SNOOZE_PATTERNS)


def _extract_mentions_from_message(message: dict) -> list[dict]:
    """Extract human @mentions from a raw Google Chat message dict."""
    mentions = []
    for annotation in message.get("annotations", []):
        if annotation.get("type") == "USER_MENTION":
            user    = annotation.get("userMention", {}).get("user", {})
            display = user.get("displayName", "")
            user_id = user.get("name", "")  # e.g. "users/123456789"
            if display and user.get("type") != "BOT":
                mentions.append({"display_name": display, "user_id": user_id})
    return mentions


# ── Per-user status helpers ───────────────────────────────────────────────────

_INDIVIDUAL_TS_FMT = "%m/%d %H:%M PST"   # → "04/20 14:30 PST" (kept for reference)
_BLOCK_TS_FMT      = "%m/%d %H:%M"        # → "04/20 17:05" — compact block format


def _is_per_user_status(status_str: str) -> bool:
    """True if status uses multi-line block format or legacy comma per-user format."""
    return "\n" in status_str or (":" in status_str and ("✅" in status_str or "🏃" in status_str))


def _parse_per_user_blocks(status_str: str) -> dict[str, dict]:
    """
    Parse the new multi-line block status format into per-user data.

    Input format (as stored in the Status cell):
        Michael Kay:
        - ⏳ Started: 04/20 17:05
        - ✅ Finished: 04/20 17:07
        Tiffany:
        - ⏳ Started: 04/20 16:50
        - 🏃 In Progress
        Ivan:
        - ⏳ Pending

    Returns {name: {'started': ts_or_None, 'finished': ts_or_None}}.
    """
    result: dict[str, dict] = {}
    if not status_str:
        return result
    current_name: str | None = None
    for line in status_str.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and not line.startswith("-"):
            current_name = line[:-1].strip()
            result[current_name] = {"started": None, "finished": None}
        elif current_name and line.startswith("- "):
            content = line[2:].strip()
            if "⏳ Started:" in content:
                result[current_name]["started"] = content.split("Started:", 1)[1].strip()
            elif "✅ Finished:" in content:
                result[current_name]["finished"] = content.split("Finished:", 1)[1].strip()
    return result


def _parse_status_to_per_user(status_str: str) -> dict[str, dict]:
    """
    Unified parser — converts any status format to per-user dict.

    Handles:
      • New block format (contains newlines) → parsed directly.
      • Legacy comma format ('Name: ✅ (ts), Name2: 🏃') → migrated.
      • Global status ('🏃 In Progress') → returns empty dict (no per-user data yet).

    Returns {name: {'started': ts_or_None, 'finished': ts_or_None}}.
    """
    if "\n" in status_str:
        return _parse_per_user_blocks(status_str)
    result: dict[str, dict] = {}
    if not _is_per_user_status(status_str):
        return result
    for part in status_str.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name = part.split(":")[0].strip()
        if not name:
            continue
        ts_match = re.search(r"\(([^)]+)\)", part)
        ts = ts_match.group(1) if ts_match else ""
        if "✅" in part:
            result[name] = {"started": ts, "finished": ts}
        else:
            result[name] = {"started": None, "finished": None}
    return result


def _parse_completed_assignees(status_str: str) -> dict[str, str]:
    """Return {name: timestamp_str} for names already finished (✅).

    Understands both new block format and legacy comma-separated format.
    """
    per_user = _parse_status_to_per_user(status_str)
    return {name: (data.get("finished") or "") for name, data in per_user.items() if data.get("finished")}


def _build_block_status(all_assignees: list[str], per_user_data: dict[str, dict]) -> str:
    """
    Build the multi-line block status string written to the Status cell (Col G).

    Example output for 3 assignees:
        Michael Kay:
        - ⏳ Started: 04/20 17:05
        - ✅ Finished: 04/20 17:07
        Tiffany:
        - ⏳ Started: 04/20 16:50
        - 🏃 In Progress
        Ivan:
        - ⏳ Pending
    """
    data_lower = {k.lower(): v for k, v in per_user_data.items()}
    blocks = []
    for name in all_assignees:
        data     = data_lower.get(name.lower(), {})
        started  = data.get("started")
        finished = data.get("finished")
        lines    = [f"{name}:"]
        if started:
            lines.append(f"- ⏳ Started: {started}")
            lines.append(f"- ✅ Finished: {finished}" if finished else "- 🏃 In Progress")
        else:
            lines.append("- ⏳ Pending")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _build_per_user_status(assignees: list[str], completed: dict[str, str]) -> str:
    """Legacy comma-format builder — kept for backward compatibility."""
    completed_lower = {c.lower(): ts for c, ts in completed.items()}
    parts = []
    for a in assignees:
        ts = completed_lower.get(a.lower())
        if ts is not None:
            parts.append(f"{a}: ✅ ({ts})" if ts else f"{a}: ✅")
        else:
            parts.append(f"{a}: 🏃")
    return ", ".join(parts)


def _match_assignee_name(sender_name: str, assignees: list[str]) -> str | None:
    """
    Find the canonical assignee name for sender.
    Tries exact case-insensitive match first, then first-word (first name) match.
    """
    sender_lower = sender_name.strip().lower()
    for a in assignees:
        if a.strip().lower() == sender_lower:
            return a.strip()
    sender_first = sender_lower.split()[0] if sender_lower else ""
    for a in assignees:
        if a.strip().lower().split()[0] == sender_first:
            return a.strip()
    return None


# ── Sheet status helper ───────────────────────────────────────────────────────

def _update_sheet_status(
    ws: gspread.Worksheet, thread_id: str, status: str, sender_name: str = ""
) -> bool:
    """Update the Status cell for a task row identified by thread_id."""
    row_idx = _find_task_row(ws, thread_id, sender_name)
    if row_idx is None:
        return False
    col_status, _ = _find_header_cols(ws)
    try:
        ws.update([[status]], f"{col_status}{row_idx}", value_input_option="USER_ENTERED")
        log.info("[TaskTracker] Sheet status → %s (row=%d thread=%s)", status, row_idx, thread_id)
        return True
    except Exception as e:
        log.error("[TaskTracker] _update_sheet_status failed: %s", e)
        return False


# ── Focus mode handlers ───────────────────────────────────────────────────────

def _handle_acknowledgment(
    thread_id: str, space_name: str, sender_name: str, sender_id: str, now: datetime,
) -> dict | None:
    """
    Enter Focus Mode and write the block-format started entry to the sheet.

    Steps must all succeed before a confirmation reply is sent:
      1. Locate the task row via multi-strategy thread ID matching.
      2. Write the timestamped start status via update_cell (direct, no batching).
      3. Enter focus mode in SQLite (cancel nag timers, start 45-min deadline).
      4. Update the Live Status 'Last Active' timestamp (PST).
      5. Send the confirmation reply.
    """
    # ── Step 1: locate the task row ───────────────────────────────────────────
    try:
        ws = _get_worksheet()
        col_status, _ = _find_header_cols(ws)
        col_status_num = _col_index(col_status)
        row_idx = _find_task_row(ws, thread_id, sender_name)
    except Exception as e:
        log.error("[TaskTracker] ACK: sheet connection failed: %s", e)
        return {"text": "❌ Failed to reach the sheet. Please check the logs."}

    if row_idx is None:
        print(f"[ACK] ❌ No row found — thread={thread_id!r} sender={sender_name!r}", flush=True)
        log.warning(
            "[TaskTracker] ACK: no task row found — thread=%s sender=%s", thread_id, sender_name,
        )
        return {"text": "❌ Error: Received 'ok' but couldn't find Thread ID in Sheet."}

    # ── Step 2: build block-format 'Name:\n- ⏳ Started: MM/DD HH:MM' and write ─
    try:
        ts_display = now.strftime(_BLOCK_TS_FMT)
        row_data = ws.row_values(row_idx)
        assignees_cell = row_data[5] if len(row_data) > 5 else ""
        current_status = row_data[6] if len(row_data) > 6 else ""
        all_assignees = [a.strip() for a in assignees_cell.split(",") if a.strip()]

        matched_name = _match_assignee_name(sender_name, all_assignees)
        canonical = matched_name or sender_name
        if canonical not in all_assignees:
            all_assignees.append(canonical)

        # Parse current status (any format) into per-user data, mark sender started
        per_user_data = _parse_status_to_per_user(current_status)
        found_key = next((k for k in per_user_data if k.lower() == canonical.lower()), None)
        if found_key:
            per_user_data[found_key]["started"] = ts_display
        else:
            per_user_data[canonical] = {"started": ts_display, "finished": None}

        new_status = _build_block_status(all_assignees, per_user_data)

        ws.update_cell(row_idx, col_status_num, new_status)
        print(
            f"[ACK] ✅ update_cell(row={row_idx}, col={col_status_num}, {new_status!r})",
            flush=True,
        )
        log.info(
            "[TaskTracker] ACK sheet status → %s (row=%d thread=%s sender=%s)",
            new_status, row_idx, thread_id, sender_name,
        )
    except Exception as e:
        log.error("[TaskTracker] ACK sheet update failed: %s", e)
        return {"text": "❌ Failed to update sheet status. Please check the logs."}

    # ── Step 3: enter focus mode in SQLite (best-effort — sheet is authoritative) ─
    focus_deadline = (now + timedelta(seconds=FOCUS_SECONDS)).isoformat()
    count = db.enter_focus_mode(thread_id, sender_name, focus_deadline)
    log.info(
        "[TaskTracker] Focus mode DB — assignee=%s timers_updated=%d thread=%s",
        sender_name, count, thread_id,
    )

    # ── Step 4: update Live Status 'Last Active' (PST) ───────────────────────
    try:
        if state.gsheet:
            state.gsheet.update_last_active(sender_name)
    except Exception as _ls_err:
        log.debug("[TaskTracker] Live Status update skipped for %s: %s", sender_name, _ls_err)

    # ── Step 5: confirm only after the write is verified ─────────────────────
    return {"text": f"✅ {canonical}'s journey started at *{ts_display}* — Sheet updated with 🏃 In Progress."}


def _handle_snooze(
    thread_id: str, sender_name: str, sender_id: str, now: datetime,
) -> dict | None:
    """
    Snooze (reset) the 45-min focus timer for sender's active focus timers.
    Returns bot reply dict if there were active focus timers, else None.
    """
    new_deadline = (now + timedelta(seconds=FOCUS_SECONDS)).isoformat()
    count = db.snooze_focus_timer(thread_id, sender_name, new_deadline)
    if count == 0:
        return None  # No active focus timers for this sender

    mention = f"<{sender_id}>" if sender_id else f"*{sender_name}*"
    log.info("[TaskTracker] Focus timer snoozed — assignee=%s thread=%s", sender_name, thread_id)
    return {"text": _FOCUS_SNOOZED.format(mention=mention)}


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
        text          = text,
        thread_id     = thread.get("name", ""),
        space_name    = space.get("name", ""),
        space_display = space.get("displayName", ""),
        sender_name   = sender.get("displayName", ""),
        sender_id     = sender.get("name", ""),
        mentions      = _extract_mentions_from_message(message),
        attachments   = raw_attachments,
        url_list      = url_list,
        now           = datetime.now(TZ),
    )


# ── Nag timer helpers ─────────────────────────────────────────────────────────

def create_nag_timers(
    thread_id: str, space_name: str, assignees: list[dict], now: datetime,
    client: str = "", city: str = "", description: str = "",
) -> None:
    """Create one nag timer row per assignee in SQLite."""
    l1 = (now + timedelta(seconds=NAG_L1_SECONDS)).isoformat()
    l2 = (now + timedelta(seconds=NAG_L2_SECONDS)).isoformat()
    l3 = (now + timedelta(seconds=NAG_L3_SECONDS)).isoformat()
    for assignee in assignees:
        name    = assignee["name"] if isinstance(assignee, dict) else assignee
        user_id = assignee.get("user_id", "") if isinstance(assignee, dict) else ""
        db.create_task_nag_timer(
            thread_id, space_name, name, l1, l2, l3,
            client=client, city=city, task_description=description,
            google_chat_id=user_id,
        )
        log.info(
            "[TaskTracker] Nag timer created — assignee=%s gchat_id=%s client=%s city=%s",
            name, user_id, client, city,
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


def close_nag_timers_for_assignee(thread_id: str, sender_name: str) -> None:
    """Mark nag timers as closed for a single assignee (partial task completion)."""
    count = db.close_task_nag_timer_for_assignee(thread_id, sender_name)
    log.info(
        "[TaskTracker] Nag timers closed for %s in thread %s (count=%d)",
        sender_name, thread_id, count,
    )


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

        client         = timer.get("client", "") or "General"
        city           = timer.get("city", "") or "General"
        description    = timer.get("task_description", "") or "—"
        google_chat_id = timer.get("google_chat_id", "") or ""
        mention        = f"<{google_chat_id}>" if google_chat_id else f"*{assignee}*"

        if level == 1:
            text = _NAG_L1.format(mention=mention, client=client, city=city, description=description)
        elif level == 2:
            text = _NAG_L2.format(mention=mention, client=client, city=city, description=description)
        else:
            text = _NAG_L3.format(mention=mention, client=client, city=city, description=description)

        try:
            gchat_sender.reply_to_thread(space_name, thread_id, text)
            db.mark_nag_level_sent(timer_id, level)
            log.info(
                "[TaskTracker] Nag L%d fired — assignee=%s thread=%s",
                level, assignee, thread_id,
            )
        except Exception as e:
            log.error("[TaskTracker] Nag alert failed (L%d, %s): %s", level, assignee, e)
            continue

        # L2: Telegram DM to assignee — runs after GChat send and mark so a
        # Telegram failure never blocks escalation or causes re-fire.
        if level == 2:
            try:
                tg_text = _NAG_L2_TELEGRAM.format(
                    name=assignee, client=client, city=city, description=description
                )
                telegram_bot.send_alert(assignee, tg_text)
            except Exception as tg_err:
                log.warning("[TaskTracker] L2 Telegram alert failed for %s: %s", assignee, tg_err)

        # L3: Telegram escalation to Michael — runs after mark so a Telegram
        # failure never causes the level to re-fire on the next scheduler tick.
        if level == 3:
            try:
                tg_text = _NAG_L3_TELEGRAM.format(
                    name=assignee, client=client, city=city, description=description,
                    space=space_name, thread=thread_id,
                )
                telegram_bot.send_alert("Michael", tg_text)
            except Exception as tg_err:
                log.warning("[TaskTracker] L3 Telegram alert failed: %s", tg_err)


def check_and_fire_focus_checks() -> None:
    """
    Called by APScheduler every 15 seconds.

    Phase 1 — send the 45-min check-in question for expired focus timers.
    Phase 2 — resume urgent nag alerts when the 10-min no-reply window expires.
    """
    now     = datetime.now(TZ)
    now_iso = now.isoformat()

    # Phase 1: 45-min focus deadline expired → send check-in question
    for timer in db.get_timers_needing_focus_check(now_iso):
        timer_id       = timer["id"]
        space_name     = timer["space_name"]
        thread_id      = timer["thread_id"]
        assignee       = timer["assignee"]
        google_chat_id = timer.get("google_chat_id", "") or ""
        client         = timer.get("client", "") or "General"
        city           = timer.get("city", "") or "General"
        description    = timer.get("task_description", "") or "—"
        mention        = f"<{google_chat_id}>" if google_chat_id else f"*{assignee}*"

        no_reply_deadline = (now + timedelta(seconds=FOCUS_NO_REPLY_SECONDS)).isoformat()
        text = _FOCUS_CHECK.format(
            mention=mention, client=client, city=city, description=description
        )
        try:
            gchat_sender.reply_to_thread(space_name, thread_id, text)
            db.mark_focus_check_sent(timer_id, no_reply_deadline)
            log.info(
                "[TaskTracker] Focus check sent — assignee=%s thread=%s",
                assignee, thread_id,
            )
        except Exception as e:
            log.error("[TaskTracker] Focus check send failed (id=%d): %s", timer_id, e)

    # Phase 2: 10-min no-reply window expired → resume urgent nag alerts
    for timer in db.get_timers_focus_no_reply(now_iso):
        timer_id       = timer["id"]
        space_name     = timer["space_name"]
        thread_id      = timer["thread_id"]
        assignee       = timer["assignee"]
        google_chat_id = timer.get("google_chat_id", "") or ""
        client         = timer.get("client", "") or "General"
        city           = timer.get("city", "") or "General"
        description    = timer.get("task_description", "") or "—"
        mention        = f"<{google_chat_id}>" if google_chat_id else f"*{assignee}*"

        resume_text = _FOCUS_NOREPLY_RESUME.format(
            mention=mention, client=client, city=city, description=description
        )
        try:
            gchat_sender.reply_to_thread(space_name, thread_id, resume_text)
        except Exception as e:
            log.warning("[TaskTracker] Focus resume notify failed (id=%d): %s", timer_id, e)

        db.exit_focus_resume_nag(timer_id, now_iso)
        log.info(
            "[TaskTracker] Focus no-reply → resumed L3 nag — assignee=%s thread=%s",
            assignee, thread_id,
        )


# ── Sheet write with strict verification ─────────────────────────────────────

def update_task_status(
    ws: gspread.Worksheet,
    thread_id: str,
    sender_name: str,
    url_list: list[str],
    now: datetime,
) -> tuple[dict, bool, str]:
    """
    Mark the sender's portion of a task complete in the Google Sheet.

    Returns (reply_dict, all_done, now_str):
      • all_done=True  → every assignee is done; caller should close all nag timers.
      • all_done=False → partial; caller should close only this sender's nag timers.
      • now_str        → full ISO-style timestamp for syncing Live Status Last Active.

    Each assignee gets a block: 'Name:\n- ⏳ Started: MM/DD HH:MM\n- ✅ Finished: MM/DD HH:MM'.
    CompletedAt (Col H/I) is written ONLY when the last assignee finishes.

    Strict Verification: success reply only returned after the Sheets API write succeeds.
    """
    now_str    = now.strftime("%Y-%m-%d %H:%M:%S")
    ts_display = now.strftime(_BLOCK_TS_FMT)

    row_idx = _find_task_row(ws, thread_id, sender_name)
    if row_idx is None:
        print(f"[DONE] ❌ No row found — thread={thread_id!r} sender={sender_name!r}", flush=True)
        log.warning(
            "[TaskTracker] update_task_status — no sheet row matched thread '%s'", thread_id
        )
        return {"text": "❌ Error: Could not find this task in the sheet."}, False, now_str

    print(f"[DONE] ▶ Found row={row_idx} for thread={thread_id!r} sender={sender_name!r}", flush=True)
    col_status, col_completed = _find_header_cols(ws)

    try:
        row_data = ws.row_values(row_idx)

        # Parse assignee list from column F and current status from column G
        assignees_cell  = row_data[5] if len(row_data) > 5 else ""
        current_status  = row_data[6] if len(row_data) > 6 else ""
        all_assignees   = [a.strip() for a in assignees_cell.split(",") if a.strip()]

        # Build per-user data from current status (new block format or legacy migration)
        per_user_data = _parse_status_to_per_user(current_status)

        # Canonicalize sender against stored assignee list, then record their finish time
        matched_name = _match_assignee_name(sender_name, all_assignees)
        canonical    = matched_name or sender_name
        if canonical not in all_assignees:
            all_assignees.append(canonical)

        found_key = next((k for k in per_user_data if k.lower() == canonical.lower()), None)
        if found_key:
            if not per_user_data[found_key].get("started"):
                per_user_data[found_key]["started"] = ts_display
            per_user_data[found_key]["finished"] = ts_display
        else:
            per_user_data[canonical] = {"started": ts_display, "finished": ts_display}

        finished_names = {k.lower() for k, v in per_user_data.items() if v.get("finished")}
        all_done = bool(all_assignees) and all(a.lower() in finished_names for a in all_assignees)

        new_status     = _build_block_status(all_assignees, per_user_data)
        status_col_num = _col_index(col_status)

        if all_done:
            # Every assignee is done → write block status + CompletedAt + Duration + SPARKLINE + URLs
            start_col = _URL_START_COL
            while start_col < len(row_data) and row_data[start_col]:
                start_col += 1

            ws.update_cell(row_idx, status_col_num, new_status)
            print(f"[DONE] ✅ update_cell(row={row_idx}, col={status_col_num}, {new_status!r})", flush=True)

            # Compute duration in minutes from Requested At (col H, index 7)
            try:
                requested_at_str = row_data[7] if len(row_data) > 7 else ""
                duration_minutes = round(
                    (now - datetime.strptime(requested_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)).total_seconds() / 60
                ) if requested_at_str else 0
            except Exception:
                duration_minutes = 0

            j_ref = f"J{row_idx}"
            sparkline = (
                f'=SPARKLINE({j_ref},{{"charttype","bar";"max",60;"color1",'
                f'IF({j_ref}<30,"green",IF({j_ref}<60,"yellow","red"))}})'
            )

            # CompletedAt + Duration + Visual Progress + URL columns — written only on final completion
            updates = [
                {"range": f"{col_completed}{row_idx}", "values": [[now_str]]},
                {"range": f"J{row_idx}",               "values": [[duration_minutes]]},
                {"range": f"K{row_idx}",               "values": [[sparkline]]},
            ]
            for i, url in enumerate(url_list):
                updates.append({
                    "range":  f"{_col_letter(start_col + i)}{row_idx}",
                    "values": [[url]],
                })
            if updates:
                ws.batch_update(updates, value_input_option="USER_ENTERED")

            data_lower     = {k.lower(): v for k, v in per_user_data.items()}
            summary        = ", ".join(
                f"{a} ✅ ({data_lower.get(a.lower(), {}).get('finished', '')})"
                for a in all_assignees
            )
            assets_preview = "\n".join(url_list) if url_list else "—"
            reply = {
                "text": (
                    f"✅ *Task Fully Completed!*\n"
                    f"👥 All done: *{summary}*\n"
                    f"🕐 Completed at: {ts_display}\n"
                    f"📎 Final assets ({len(url_list)}):\n{assets_preview}"
                )
            }
        else:
            # Partial completion — update Status cell only; CompletedAt stays blank
            ws.update_cell(row_idx, status_col_num, new_status)
            print(f"[DONE] ✅ update_cell(row={row_idx}, col={status_col_num}, {new_status!r})", flush=True)

            pending     = [a for a in all_assignees if a.lower() not in finished_names]
            pending_str = ", ".join(f"*{p}*" for p in pending)
            reply = {
                "text": (
                    f"✅ *{canonical}* marked their part as done! _(at {ts_display})_\n"
                    f"⏳ Still pending: {pending_str}\n"
                    f"_SLA nag timers continue for pending assignees._"
                )
            }

        log.info(
            "[TaskTracker] Completion update — row=%d sender=%s all_done=%s ts=%s",
            row_idx, sender_name, all_done, ts_display,
        )
        return reply, all_done, now_str

    except Exception as e:
        log.error(
            "[TaskTracker] update_task_status failed (row=%d, thread=%s): %s",
            row_idx, thread_id, e,
        )
        return {"text": "❌ Failed to update sheet. Please check the logs."}, False, now_str


# ── Public API ────────────────────────────────────────────────────────────────

def handle_task_event(
    *,
    text: str,
    thread_id: str,
    space_name: str,
    space_display: str = "",
    sender_name: str,
    sender_id: str = "",
    mentions: list[dict],
    attachments: list[dict],
    url_list: list[str],
    now: datetime,
) -> dict | None:
    """
    Process a Google Chat message for task lifecycle events.

    Returns dict {"text": <Business English bot reply>} or None.

    Scenario A — Completion   : "done" / "completed" / etc.  → close task
    Scenario C — Acknowledgment: "ok" / "got it" / etc.       → enter Focus Mode
    Scenario D — Snooze        : "still working" / "doing it" → reset 45-min timer
    Scenario B — New Task      : @best mentioned               → register task
    """
    print(
        f"[TASK_HANDLER] ▶ text={text!r} | "
        f"is_ack={is_acknowledgment(text)} | is_done={_is_completion(text)} | "
        f"thread={thread_id!r} | sender={sender_name!r}",
        flush=True,
    )

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

    # ── Scenario C: Acknowledgment → Focus Mode (pre-processed first) ───────────
    # Checked before completion so "ok" is never misrouted — sheet write is
    # mandatory; if the write fails, the reply is an error (never "Focus Mode ON").
    if is_acknowledgment(text):
        result = _handle_acknowledgment(thread_id, space_name, sender_name, sender_id, now)
        if result:
            return result

    # ── Scenario A: Task Completion ───────────────────────────────────────────
    if _is_completion(text):
        log.info("[TaskTracker] Scenario A — thread_id=%s sender=%s url_list=%s",
                 thread_id, sender_name, url_list)

        result, all_done, completion_ts = update_task_status(ws, thread_id, sender_name, url_list, now)

        if result["text"].startswith("✅"):
            if all_done:
                close_nag_timers(thread_id)
            else:
                close_nag_timers_for_assignee(thread_id, sender_name)
            try:
                if state.gsheet:
                    state.gsheet.update_last_active(sender_name, ts=completion_ts)
            except Exception as _ls_err:
                log.debug("[TaskTracker] Live Status update skipped for %s: %s", sender_name, _ls_err)

        return result

    # ── Scenario D: Snooze Focus Timer ───────────────────────────────────────
    if is_snooze(text):
        result = _handle_snooze(thread_id, sender_name, sender_id, now)
        if result:
            return result

    # ── Scenario B: New Task Registration ─────────────────────────────────────
    if not _bot_was_mentioned(text):
        return None

    parsed    = _parse_task_text(text, mentions)
    assignees = parsed["assignees"]

    if not assignees:
        log.debug("[TaskTracker] @best triggered but no assignees found")
        return None

    assignee_str = ", ".join(a["name"] for a in assignees)

    log.info(
        "[TaskTracker] Scenario B — %d URL(s) → cols L+ : %s",
        len(url_list), url_list,
    )

    # Build HYPERLINK formula for col B (Space column).
    # space_name is the full resource path, e.g. "spaces/AAAXXX".
    # thread_id is the full thread path, e.g. "spaces/AAAXXX/threads/BBBYYY".
    space_id_raw   = space_name.split("/")[-1] if "/" in space_name else space_name
    thread_key_raw = thread_id.rsplit("/", 1)[-1] if "/" in thread_id else thread_id
    link_text      = (space_display or "🔗 View Thread").replace('"', "'")
    space_hyperlink = (
        f'=HYPERLINK("https://mail.google.com/chat/u/0/'
        f'#chat/space/{space_id_raw}/{thread_key_raw}", "{link_text}")'
    )

    # Fixed cols A–K, then one URL per cell from L onwards (no limit)
    new_row = [
        date_str, space_hyperlink,
        parsed["client"], parsed["city"], parsed["description"],
        assignee_str,
        "🏃 In Progress", now_str,
        "",          # Completed At (col I)
        "",          # Duration (min) (col J) — filled on completion
        "",          # Visual Progress (col K) — filled on completion
        *url_list,   # cols L, M, N, … — one clean URL each
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

    create_nag_timers(
        thread_id, space_name, assignees, now,
        client=parsed["client"], city=parsed["city"], description=parsed["description"],
    )

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
