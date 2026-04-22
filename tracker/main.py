"""
Agency Remote Tracking System — Phase 1 Main Server
FastAPI webhook receiver for Google Chat events.

Endpoints:
  POST /webhook/google-chat   → Google Chat Bot events
  GET  /health                → Status check
"""
import os
import re
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import time
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
log = logging.getLogger(__name__)

import db
import sheets_client
import telegram_bot
from checkin_parser import parse_checkin, parse_eod_links
from scheduler import create_scheduler
from config import (
    TEAM_MEMBERS, TIMEZONE,
    DAILY_REPORT_SPACE_KEYWORD,
    CHECKIN_WINDOW_START, CHECKIN_WINDOW_END,
    EOD_WINDOW_START, EOD_WINDOW_END,
    SLA_SECONDS,
    CHAT_ARCHIVE_ENABLED,
)
from gsheet_handler import GSheetHandler
import ai_engine
import state
import task_tracker
import gchat_sender
import command_router  # V2 @best 커맨드 라우터

from wiki.interceptor import WikiInterceptor, WikiModelRouter
from wiki.validator import WikiValidator

_wiki_interceptor: WikiInterceptor | None = None
_wiki_validator:   WikiValidator   | None = None
_wiki_router:      WikiModelRouter | None = None

# ─────────────────────────────────────────
# Admin auth
# ─────────────────────────────────────────
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")


def _verify_admin(x_admin_token: str = Header(None)):
    if not ADMIN_TOKEN:
        raise HTTPException(503, detail="Admin disabled (ADMIN_TOKEN not set)")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, detail="Invalid token")


# ─────────────────────────────────────────
# Startup
# ─────────────────────────────────────────
TZ = ZoneInfo(TIMEZONE)
db.init_db()

_ai_ok: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ai_ok, _wiki_interceptor, _wiki_validator, _wiki_router

    # ── AI Engine
    _ai_ok = ai_engine.init()

    # ── Google Sheets
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "1e_YQ9YBC_SCfM3Ex_rkg_f5NWlr5nOF9LBk3GT2TwZ8")

    if not spreadsheet_id:
        log.error("[GSheet] ❌ SPREADSHEET_ID is not set — Sheets integration disabled.")
    else:
        try:
            state.gsheet = GSheetHandler(credentials_path=creds_path, spreadsheet_id=spreadsheet_id)
            state.gsheet_ok = True
            log.info("[GSheet] ✅ Connected to Google Sheets successfully.")
        except Exception as exc:
            log.error("[GSheet] ❌ Failed to connect to Google Sheets: %s", exc)

    # ── Wiki
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        log.warning("[Wiki] ⚠️ OPENROUTER_API_KEY not set — wiki entity detection disabled")
    else:
        try:
            _wiki_router      = WikiModelRouter(api_key=openrouter_key)
            _wiki_interceptor = WikiInterceptor(api_key=openrouter_key)
            _wiki_validator   = WikiValidator(gchat_sender, sheets_client)
            log.info("[Wiki] ✅ WikiInterceptor + WikiValidator + WikiModelRouter ready")
        except Exception as exc:
            log.error("[Wiki] ❌ Startup failed: %s", exc)

    # ── Provision Sheets tabs
    print("[SHEETS] startup_provision_tabs: creating required tabs...", flush=True)
    log.info("[SHEETS] startup_provision_tabs: creating required tabs...")

    results: dict[str, str] = {}
    for tab_name, headers in [
        (sheets_client.TAB_CLIENT_WIKI,  sheets_client._WIKI_HEADERS),
        (sheets_client.TAB_CHAT_ARCHIVE, sheets_client._ARCHIVE_HEADERS),
    ]:
        try:
            sheets_client._get_or_create_tab(tab_name, headers)
            results[tab_name] = "✅ OK"
        except Exception as exc:
            results[tab_name] = f"❌ FAILED: {exc}"

    for tab, status in results.items():
        print(f"[SHEETS] {tab}: {status}", flush=True)
        log.info("[SHEETS] %s: %s", tab, status)

    try:
        command_router.provision_tabs()
        log.info("[SHEETS] V2 tabs (Update_Tracker / Task_Tracker) ready")
    except Exception as exc:
        log.warning("[SHEETS] V2 tab provisioning failed (continuing): %s", exc)

    # ── touch_space flush loop — drains in-memory last_active cache to SQLite every 60s
    async def _flush_loop():
        while True:
            await asyncio.sleep(60)
            try:
                n = db.flush_touch_cache()
                if n:
                    log.debug("[Spaces] flush_touch_cache: %d row(s) written", n)
            except Exception:
                log.exception("[Spaces] flush_touch_cache failed")

    # ── find_task_row stats flush loop — 30s interval (accuracy matters more than spaces)
    async def _flush_find_row_loop():
        while True:
            await asyncio.sleep(30)
            try:
                n = db.flush_find_row_cache()
                if n:
                    log.debug("[FindRow] flush_find_row_cache: %d event(s) written", n)
            except Exception:
                log.exception("[FindRow] flush_find_row_cache failed")

    flush_task          = asyncio.create_task(_flush_loop())
    flush_find_row_task = asyncio.create_task(_flush_find_row_loop())

    log.info("Agency Tracker started — timezone=%s", TIMEZONE)
    yield

    for task in (flush_task, flush_find_row_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    db.flush_touch_cache()       # final drain before shutdown
    db.flush_find_row_cache()    # final drain before shutdown
    log.info("[Spaces] touch_space + find_row caches flushed on shutdown")


app = FastAPI(title="Agency Remote Tracking System", version="1.0.0", lifespan=lifespan)
scheduler = create_scheduler()
scheduler.start()
telegram_bot.start_polling()


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def _in_window(now: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    """Check if current local time falls within a (hour, minute) window."""
    t = (now.hour, now.minute)
    return start <= t <= end


_ADDON_MESSAGE_TYPES = {"MESSAGE", "HUMAN_MESSAGE_IN_SPACE", "DIRECT_MESSAGE"}
_ADDON_ADDED_TYPES   = {"ADDED_TO_SPACE"}


def _normalize_gchat_event(body: dict) -> tuple[str, dict]:
    """
    Normalize Google Chat App and Google Workspace Add-on payloads.

    Format 1 — Chat App HTTP endpoint (non-empty top-level type):
      body["type"] = "MESSAGE" | "ADDED_TO_SPACE"
      body["message"]["text"], ["sender"], ["space"], ["thread"]

    Format 2 — Workspace Add-on with explicit eventType:
      body["commonEventObject"] present
      body["chat"]["eventType"] = "MESSAGE" | "HUMAN_MESSAGE_IN_SPACE" | "ADDED_TO_SPACE"
      body["chat"]["message"]["text"]

    Format 3 — Workspace Add-on with messagePayload (no eventType):
      body["commonEventObject"] present
      body["chat"]["messagePayload"]["message"]["text"]
      body["chat"]["user"] = sender
      eventType is absent or empty — presence of messagePayload signals a MESSAGE
    """
    log.info("[GChat][norm] Top-level keys: %s", sorted(body.keys()))

    # ── Format 1: Standard Chat App — only when type is a non-empty string
    raw_type = body.get("type", "")
    if raw_type:
        log.info("[GChat][norm] Format1 → type=%s", raw_type)
        return raw_type, body

    # ── Formats 2 & 3: Workspace Add-on (commonEventObject present)
    if "commonEventObject" in body:
        chat = body.get("chat", {})
        log.info("[GChat][norm] Add-on format — chat keys: %s", sorted(chat.keys()))

        raw_event = chat.get("eventType", "")
        log.info("[GChat][norm] chat.eventType=%r", raw_event)

        user  = chat.get("user", {})
        space = chat.get("space", {})

        # ── Format 3: messagePayload path (no eventType)
        msg_payload = chat.get("messagePayload", {})
        if msg_payload:
            message = dict(msg_payload.get("message", {}))
            # messagePayload itself may carry space/thread at top level
            if not message.get("space") and msg_payload.get("space"):
                message["space"] = msg_payload["space"]
            if not message.get("sender") and user:
                message["sender"] = user
            if not message.get("space") and space:
                message["space"] = space
            event_type = "MESSAGE"
            normalized = {"type": event_type, "space": space or message.get("space", {}),
                          "user": user, "message": message}
            log.info("[GChat][norm] Format3 (messagePayload) → type=MESSAGE, text=%r",
                     message.get("text", "")[:80])
            return event_type, normalized

        # ── Format 2: explicit eventType path
        if raw_event in _ADDON_MESSAGE_TYPES:
            event_type = "MESSAGE"
        elif raw_event in _ADDON_ADDED_TYPES:
            event_type = "ADDED_TO_SPACE"
        else:
            event_type = raw_event

        message = dict(chat.get("message", {}))
        if not message.get("sender") and user:
            message["sender"] = user
        if not message.get("space") and space:
            message["space"] = space

        normalized = {"type": event_type, "space": space, "user": user, "message": message}
        log.info("[GChat][norm] Format2 (eventType) → type=%s, text=%r",
                 event_type, message.get("text", "")[:80])
        return event_type, normalized

    log.warning("[GChat][norm] Unknown payload — keys=%s", sorted(body.keys()))
    return "", body


def _is_daily_report_space(space_display: str) -> bool:
    return DAILY_REPORT_SPACE_KEYWORD.lower() in space_display.lower()


def _resolve_member_name(display_name: str) -> str | None:
    """Match a Google Chat display name to a configured team member name."""
    for name in TEAM_MEMBERS:
        if name.lower() in display_name.lower() or display_name.lower() in name.lower():
            return name
    return None


_URL_RE = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)


def _extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text)


def _classify_message(text: str, attachments: list) -> tuple[str, str, str]:
    """Inspect message content and attachments.

    Returns (msg_type, attachment_name, file_link) where:
      msg_type       — Text | URL | Image | PDF | File | Mixed
      attachment_name — semicolon-joined filenames
      file_link       — semicolon-joined Drive/download links + extracted URLs
    """
    att_names: list[str] = []
    att_links: list[str] = []
    att_type_tags: list[str] = []

    for att in attachments:
        name    = att.get("contentName") or att.get("name") or ""
        mime    = att.get("contentType") or att.get("mimeType") or ""
        drv_id  = att.get("driveDataRef", {}).get("driveFileId", "")
        dl_uri  = att.get("downloadUri", "")

        if name:
            att_names.append(name)
        if drv_id:
            att_links.append(f"https://drive.google.com/file/d/{drv_id}/view")
        elif dl_uri:
            att_links.append(dl_uri)

        name_lower = name.lower()
        if mime.startswith("image/") or any(name_lower.endswith(x) for x in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            att_type_tags.append("Image")
        elif mime == "application/pdf" or name_lower.endswith(".pdf"):
            att_type_tags.append("PDF")
        elif name or mime:
            att_type_tags.append("File")

    urls_in_text = _extract_urls(text)
    has_att      = bool(att_type_tags)
    has_urls     = bool(urls_in_text)
    has_plain    = bool(text.strip()) and len(text.strip()) > len(" ".join(urls_in_text))

    if has_att:
        unique_tags = list(dict.fromkeys(att_type_tags))
        primary = unique_tags[0] if len(unique_tags) == 1 else "File"
        msg_type = "Mixed" if has_urls else primary
    elif has_urls:
        msg_type = "Mixed" if has_plain else "URL"
    else:
        msg_type = "Text"

    all_links = att_links + [u for u in urls_in_text if u not in att_links]
    return msg_type, "; ".join(att_names), "; ".join(all_links)


def _extract_mentions(message: dict) -> list[dict]:
    """
    Extract @mentioned users from a Google Chat message.
    Returns list of {google_id, display_name} dicts.
    """
    mentions = []
    for annotation in message.get("annotations", []):
        if annotation.get("type") == "USER_MENTION":
            user = annotation.get("userMention", {}).get("user", {})
            google_id = user.get("name", "")
            display = user.get("displayName", "")
            if google_id and display and user.get("type") != "BOT":
                mentions.append({"google_id": google_id, "display_name": display})
    return mentions


# ─────────────────────────────────────────
# Google Chat Webhook
# ─────────────────────────────────────────

@app.post("/webhook/google-chat")
async def google_chat_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Google Chat event receiver.

    ALWAYS returns 200 immediately — heavy processing runs in BackgroundTasks.
    This prevents Google Chat from showing "Not responding" due to slow Sheets/DB calls.
    """
    raw_body = await request.body()
    auth_header = request.headers.get("Authorization", "MISSING")
    log.info(
        "[GChat] ▶ Incoming | auth=%s... | body=%s",
        auth_header[:30],
        raw_body[:1500].decode("utf-8", errors="replace"),
    )

    if not auth_header.startswith("Bearer "):
        log.warning("[GChat] ⚠️ No Bearer token — proceeding (dev mode). Source: %s",
                    request.client)

    try:
        import json
        body = json.loads(raw_body)
    except Exception:
        log.error("[GChat] ❌ Failed to parse JSON body")
        return {}

    event_type, norm_body = _normalize_gchat_event(body)
    log.info("[GChat] Event type: %s", event_type)

    # ── ADDED_TO_SPACE — respond synchronously so Google Chat gets the greeting
    if event_type == "ADDED_TO_SPACE":
        space      = norm_body.get("space", {})
        space_id   = space.get("name", "UNKNOWN")
        space_name = space.get("displayName", "UNKNOWN")
        space_type = space.get("type", "UNKNOWN")

        # Register this space for multi-space SLA tracking
        try:
            db.upsert_space(space_id, space_name, space_type)
        except Exception as _sp_err:
            log.warning("[Spaces] upsert_space failed: %s", _sp_err)

        log.info("=" * 60)
        log.info("[GChat] ✅ Bot added to space: '%s'", space_name)
        log.info("[GChat] 📋 SPACE ID   →  %s", space_id)
        log.info("[GChat] 📋 SPACE TYPE →  %s", space_type)
        log.info("[GChat] ↳ Paste into .env → DAILY_REPORT_SPACE_ID=%s", space_id)
        log.info("=" * 60)

        return {
            "text": (
                "✅ *MARTS Tracker is now active in this space.*\n"
                "I will monitor daily check-ins and @mention response times.\n\n"
                f"🆔 Space ID logged: `{space_id}`"
            )
        }

    # ── CARD_CLICKED — must be handled synchronously (Google Chat reads the body)
    if event_type == "CARD_CLICKED":
        return _handle_card_click(norm_body)

    # ── MESSAGE — return 200 immediately, process in background
    if event_type == "MESSAGE":
        background_tasks.add_task(_handle_message, norm_body)
        return {}

    return {}


def _handle_card_click(body: dict) -> dict:
    """Synchronous CARD_CLICKED dispatcher — delegates to WikiValidator."""
    if _wiki_validator is None:
        log.warning("[GChat] CARD_CLICKED received but WikiValidator not initialised")
        return {"text": "⚠️ Wiki validator is not available."}
    try:
        return _wiki_validator.handle_card_click(body)
    except Exception as exc:
        log.error("[GChat] CARD_CLICKED handler error: %s", exc)
        return {"text": "⚠️ An error occurred while processing your action."}


def _handle_message(body: dict) -> None:
    """
    Sync handler — runs AFTER the 200 response is sent.

    Dispatch order (strict priority):
      1. Task Tracker  — @best commands and task completions  [FIRST — early return]
      2. Check-in      — 09:00 daily goals in Daily Report thread
      3. EOD           — 16:45 result links in Daily Report thread
      4. SLA           — resolve existing timers + start new ones for @mentions
    """
    _t0 = time.monotonic()
    message  = body.get("message", {})
    sender   = message.get("sender", {})
    space    = message.get("space", {})
    thread   = message.get("thread", {})

    sender_google_id = sender.get("name", "")
    sender_display   = sender.get("displayName", "")
    sender_type      = sender.get("type", "HUMAN")
    space_name       = space.get("name", "")
    space_display    = space.get("displayName", "")
    thread_key       = thread.get("name", "")
    text             = message.get("text", "").strip()
    now_local        = datetime.now(TZ)

    if sender_type == "BOT":
        return

    log.info("[GChat] Message from %s in '%s'", sender_display, space_display)

    # Update last_active timestamp for this space (multi-space SLA tracking)
    if space_name:
        try:
            db.touch_space(space_name)
        except Exception:
            pass  # upsert_space may not have been called yet for this space
        # Auto-register spaces we haven't seen via ADDED_TO_SPACE (e.g. existing spaces)
        try:
            db.upsert_space(space_name, space_display)
        except Exception:
            pass

    matched_name = _resolve_member_name(sender_display)
    if matched_name and sender_google_id:
        db.upsert_google_chat_id(matched_name, sender_google_id, sender_display)

    # ── [0] WIKI EDIT REPLY — highest priority when thread is awaiting correction
    if (
        _wiki_validator is not None
        and thread_key
        and _wiki_validator.is_awaiting_edit(thread_key)
    ):
        consumed = _wiki_validator.handle_edit_reply(thread_key, text)
        if consumed:
            log.info("[Wiki] Edit reply consumed from %s in thread %s", sender_display, thread_key)
            return

    # ── Live Status: update Last Active (col C) for every message from a known member
    if matched_name and state.gsheet:
        try:
            state.gsheet.update_last_active(matched_name)
        except Exception as _la_err:
            log.debug("[GSheet] update_last_active silenced for %s: %s", matched_name, _la_err)

    # ── Task Nag: cancel timer on any reply, BUT skip when ACK/snooze keywords
    # are present — those are handled inside process_task_message (focus mode).
    if thread_key and matched_name:
        if not task_tracker.is_acknowledgment(text) and not task_tracker.is_snooze(text):
            task_tracker.acknowledge_nag_timers(thread_key, matched_name)

    # ── [1] V2 COMMAND ROUTER — @best 커맨드 최우선 처리
    # update / @담당자 / ! / urgent / ask / brief / undo / check
    try:
        v2_reply = command_router.dispatch(body)
    except Exception as _v2_err:
        log.error("[Router] dispatch exception: %s", _v2_err)
        v2_reply = None

    if v2_reply is not None:
        if space_name and thread_key:
            gchat_sender.reply_to_thread(space_name, thread_key, v2_reply)
        else:
            log.error(
                "[Router] V2 reply dropped — missing thread context: space=%r thread=%r",
                space_name, thread_key,
            )
        return  # V2 커맨드 처리 완료 — 이하 로직 스킵

    # ── [2] TASK TRACKER — Track A: 기존 @best / ok / done 처리 (V2 미인식 시 폴백)
    task_reply = task_tracker.process_task_message(body)
    if task_reply:
        if space_name and thread_key:
            gchat_sender.reply_to_thread(space_name, thread_key, task_reply["text"])
        else:
            log.error(
                "[GChat] Task reply dropped — missing thread context: space=%r thread=%r text=%.80s",
                space_name, thread_key, task_reply["text"],
            )
        return  # Track A: command handled — skip Chat_Archive

    # ── [2] CHAT ARCHIVE — Track B: non-command messages only
    if CHAT_ARCHIVE_ENABLED:
        try:
            _attachments = message.get("attachment") or message.get("attachments") or []
            _msg_type, _, _file_link = _classify_message(text, _attachments)
            _link = _file_link or message.get("name") or ""

            # Generate 3-word content summary when message has a URL or file attachment
            _summary = ""
            if _msg_type != "Text" and _wiki_router is not None:
                _url_list = [u for u in _file_link.split("; ") if u] if _file_link else []
                _summary = _wiki_router.summarize_content(text, _url_list)

            sheets_client.log_chat_archive(
                timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S"),
                space     = space_display,
                user      = sender_display,
                message   = text,
                link      = _link,
                summary   = _summary,
            )
        except Exception as _arc_err:
            log.debug("[Archive] Silent log suppressed: %s", _arc_err)

    # ── Date reset (daily thread keys expire at midnight)
    checkin_thread_key = db.get_state("checkin_thread_key")
    eod_thread_key     = db.get_state("eod_thread_key")
    today              = now_local.strftime("%Y-%m-%d")
    state_date         = db.get_state("checkin_date")

    if state_date and state_date != today:
        db.set_state("checkin_thread_key", "")
        db.set_state("eod_thread_key", "")
        checkin_thread_key = None
        eod_thread_key     = None

    # ── [2] CHECK-IN
    is_checkin_space  = _is_daily_report_space(space_display)
    is_checkin_window = _in_window(now_local, CHECKIN_WINDOW_START, CHECKIN_WINDOW_END)
    is_checkin_thread = checkin_thread_key and thread_key == checkin_thread_key

    if is_checkin_space and is_checkin_window and is_checkin_thread and matched_name:
        goals    = parse_checkin(text)
        time_str = now_local.strftime("%H:%M:%S")
        status   = "on-time" if _in_window(now_local, CHECKIN_WINDOW_START, (9, 15)) else "late"
        db.log_checkin(today, matched_name, time_str, goals, text, status)
        sheets_client.log_checkin(today, matched_name, time_str, goals, status)
        log.info("[Check-in] ✅ %s — %s — goals: %s", matched_name, status, goals)
        return

    # ── [3] EOD
    is_eod_window = _in_window(now_local, EOD_WINDOW_START, EOD_WINDOW_END)
    is_eod_thread = eod_thread_key and thread_key == eod_thread_key

    if is_checkin_space and is_eod_window and is_eod_thread and matched_name:
        text_links   = parse_eod_links(text)
        attachments  = message.get("attachment") or message.get("attachments") or []
        attach_urls  = [
            url for att in attachments
            for url in (
                [f"https://drive.google.com/file/d/{att.get('driveDataRef', {}).get('driveFileId', '')}/view"]
                if att.get("driveDataRef", {}).get("driveFileId")
                else [att.get("downloadUri", "")] if att.get("downloadUri")
                else []
            )
        ]
        links    = text_links + [u for u in attach_urls if u]
        time_str = now_local.strftime("%H:%M:%S")
        db.log_eod(today, matched_name, time_str, links, text)
        sheets_client.log_eod(today, matched_name, time_str, links)
        log.info("[EOD] ✅ %s — %d link(s) (%d from attachments)", matched_name, len(links), len(attach_urls))
        return

    # ── [4] SLA: resolve existing timer if tagged person replies
    if sender_google_id and thread_key:
        resolved = db.resolve_sla_timer(thread_key, sender_google_id)
        for t in resolved:
            log.info("[SLA] ✅ Resolved — %s replied in thread %s", sender_display, thread_key)
            sheets_client.log_sla_breach(
                today, now_local.strftime("%H:%M"),
                space_display, t["tagger_name"], t["tagged_name"],
                thread_key, met=True,
            )

    # ── [4] SLA: start new timers for @mentions
    mentions    = _extract_mentions(message)
    tagger_name = matched_name or sender_display

    for mention in mentions:
        tagged_name      = _resolve_member_name(mention["display_name"])
        tagged_google_id = mention["google_id"]

        if not tagged_name:
            log.debug("[SLA] Unknown mention: %s — skipping", mention["display_name"])
            continue

        deadline = datetime.now(TZ) + timedelta(seconds=SLA_SECONDS)
        timer_id = db.create_sla_timer(
            space_display, space_name, thread_key,
            tagger_name, tagged_name, tagged_google_id, deadline,
        )
        log.info(
            "[SLA] Timer started — %s tagged %s in '%s' | deadline=%s | id=%s",
            tagger_name, tagged_name, space_display,
            deadline.strftime("%H:%M:%S"), timer_id,
        )

    # ── [5] WIKI ENTITY DETECTION — run interceptor on every non-bot message
    if _wiki_interceptor is not None and _wiki_validator is not None and text and space_name and thread_key:
        try:
            extraction = _wiki_interceptor.intercept(text)
            if extraction.has_any():
                message_link = (
                    message.get("name", "")
                    or f"https://chat.google.com/room/{space_name}/{thread_key}"
                )
                _wiki_validator.prompt_verification(
                    extraction,
                    space_name,
                    thread_key,
                    sender_google_id,
                    source_link=message_link,
                )
                log.info(
                    "[Wiki] Entity detected from %s — status=%s fields=%s",
                    sender_display,
                    extraction.status,
                    [n for n, f in extraction.extracted_fields().items() if f.has_value()],
                )
        except Exception as exc:
            log.warning("[Wiki] Interception failed silently: %s", exc)

    log.info("[webhook] handler %.1fms | space=%s sender=%s", (time.monotonic() - _t0) * 1000, space_display, sender_display)


# ─────────────────────────────────────────
# Local Test Webhook (no auth — dev only)
# ─────────────────────────────────────────

@app.post("/test-webhook")
async def test_webhook(request: Request):
    """
    Auth-free endpoint for local testing before ngrok is wired.
    Simulates a Google Chat MESSAGE event with @mention.

    Usage:
        curl -s -X POST http://localhost:8000/test-webhook \
          -H "Content-Type: application/json" \
          -d '{
            "type": "MESSAGE",
            "message": {
              "sender": {"name": "users/123", "displayName": "Tiffany", "type": "HUMAN"},
              "space":  {"name": "spaces/ABC", "displayName": "1.Urgent Accounts"},
              "thread": {"name": "spaces/ABC/threads/XYZ"},
              "text": "hey @Ivan can you check this?",
              "annotations": [{
                "type": "USER_MENTION",
                "userMention": {
                  "user": {"name": "users/456", "displayName": "Ivan", "type": "HUMAN"}
                }
              }]
            }
          }'
    """
    body = await request.json()
    event_type = body.get("type", "")
    log.info("[TestWebhook] Received event_type=%s", event_type)

    if event_type == "MESSAGE":
        message      = body.get("message", {})
        sender       = message.get("sender", {})
        space        = message.get("space", {})
        thread       = message.get("thread", {})
        sender_display = sender.get("displayName", "Unknown")
        space_display  = space.get("displayName", "Unknown Space")
        space_name     = space.get("name", "")
        thread_key     = thread.get("name", "")
        sender_google_id = sender.get("name", "")
        text           = message.get("text", "").strip()
        now_local      = datetime.now(TZ)
        today          = now_local.strftime("%Y-%m-%d")

        matched_name = _resolve_member_name(sender_display)
        if matched_name and sender_google_id:
            db.upsert_google_chat_id(matched_name, sender_google_id, sender_display)

        mentions = _extract_mentions(message)
        tagger_name = matched_name or sender_display
        results = []

        for mention in mentions:
            tagged_name = _resolve_member_name(mention["display_name"])
            if not tagged_name:
                continue
            deadline = datetime.now(TZ) + timedelta(seconds=SLA_SECONDS)
            timer_id = db.create_sla_timer(
                space_display, space_name, thread_key,
                tagger_name, tagged_name, mention["google_id"], deadline
            )
            log.info(
                "[TestWebhook] Received message in thread %s mentioning %s "
                "(tagged by %s) — SLA timer started (%ds), deadline %s [timer_id=%s]",
                thread_key, tagged_name, tagger_name,
                SLA_SECONDS, deadline.strftime("%H:%M:%S"), timer_id
            )
            results.append({
                "tagged": tagged_name,
                "tagger": tagger_name,
                "thread": thread_key,
                "deadline": deadline.strftime("%H:%M:%S"),
                "timer_id": timer_id,
            })

        return {"status": "ok", "event": event_type, "space": space_display, "timers_created": results}

    return {"status": "ok", "event": event_type}


# ─────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────

@app.get("/health")
async def health():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    con = db.get_conn()
    members_total    = con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    tg_registered    = con.execute("SELECT COUNT(*) FROM members WHERE telegram_registered=1").fetchone()[0]
    checkins_today   = con.execute("SELECT COUNT(*) FROM checkins WHERE date=?", (today,)).fetchone()[0]
    eod_today        = con.execute("SELECT COUNT(*) FROM eod_submissions WHERE date=?", (today,)).fetchone()[0]
    active_timers    = con.execute("SELECT COUNT(*) FROM sla_timers WHERE resolved=0 AND telegram_pinged=0").fetchone()[0]
    con.close()

    spaces = db.get_all_spaces()
    monitored_spaces = [
        {"name": s["space_name"], "display": s["display_name"], "last_active": s["last_active"]}
        for s in spaces
    ]

    return {
        "status":              "ok",
        "timestamp":           datetime.now(TZ).isoformat(),
        "timezone":            TIMEZONE,
        "google_sheets":       "connected" if state.gsheet_ok else "disconnected",
        "ai_engine":           "ready" if _ai_ok else "disabled",
        "members_total":       members_total,
        "telegram_registered": tg_registered,
        "checkins_today":      f"{checkins_today}/{members_total}",
        "eod_today":           f"{eod_today}/{members_total}",
        "active_sla_timers":   active_timers,
        "checkin_thread":      db.get_state("checkin_thread_key") or "not set",
        "eod_thread":          db.get_state("eod_thread_key") or "not set",
        "monitored_spaces":    monitored_spaces,
        "spaces_count":        len(monitored_spaces),
    }


@app.post("/test-reply")
async def test_reply(request: Request):
    """
    Simulate the tagged person replying in a thread — cancels their active SLA timer.

    Usage:
        curl -s -X POST http://localhost:8000/test-reply \\
          -H "Content-Type: application/json" \\
          -d '{"thread_key": "spaces/ABC/threads/XYZ", "responder_google_id": "users/456"}'
    """
    body = await request.json()
    thread_key          = body.get("thread_key", "")
    responder_google_id = body.get("responder_google_id", "")

    if not thread_key or not responder_google_id:
        return {"status": "error", "detail": "thread_key and responder_google_id are required"}

    resolved = db.resolve_sla_timer(thread_key, responder_google_id)
    if resolved:
        for t in resolved:
            log.info("[TestReply] ✅ SLA timer cancelled — %s replied in thread %s (timer_id=%s)",
                     t["tagged_name"], thread_key, t["id"])
            if state.gsheet:
                state.gsheet.log_sla_breach(
                    tagger=t["tagger_name"],
                    tagged=t["tagged_name"],
                    space=t["space_display"],
                    resolved=True,
                    alert_sent=False,
                )
        return {"status": "ok", "timers_cancelled": len(resolved),
                "cancelled": [{"timer_id": t["id"], "tagged": t["tagged_name"]} for t in resolved]}

    return {"status": "ok", "timers_cancelled": 0,
            "note": "No active timers found for that thread + responder combination"}


@app.post("/admin/spaces/{space_name:path}/sla")
async def toggle_space_sla(
    space_name: str,
    enabled: bool,
    _: None = Depends(_verify_admin),
):
    """Enable or disable SLA nag alerts for a space.

    Query param: ?enabled=true|false
    Header: X-Admin-Token required.
    """
    before = db.get_sla_enabled(space_name)
    if before is None:
        raise HTTPException(404, detail=f"Space not found: {space_name}")
    db.set_sla_enabled(space_name, enabled)
    log.info(
        "[admin] SLA toggle — space=%s before=%s after=%s",
        space_name, before, enabled,
    )
    return {"space_name": space_name, "sla_enabled": enabled, "previous": before}


@app.get("/admin/find-task-row-stats")
async def get_find_task_row_stats(
    days: int = 30,
    _=Depends(_verify_admin),
):
    """Return _find_task_row() aggregate stats for the last N days.

    Flushes in-memory counters before querying so recent calls are included.
    Response shape:
      {
        "period_days": 30,
        "total_calls": 1523,
        "no_match": 4,
        "by_strategy": {
          "1": {"count": 1450, "pct": 95.2},
          ...
          "6": {"count": 3, "pct": 0.2}
        }
      }
    strategy 0 = no match; 1-6 = matched strategy number.
    """
    db.flush_find_row_cache()  # include counters not yet written to DB
    return db.get_find_task_row_stats(days)


@app.post("/admin/trigger-weekly-report")
async def trigger_weekly_report(
    background_tasks: BackgroundTasks,
    overwrite: bool = False,
    _: None = Depends(_verify_admin),
):
    """Manually trigger the weekly AI report.

    Requires X-Admin-Token header matching ADMIN_TOKEN env var.
    ?overwrite=true  — re-write existing Week_Label row in the sheet instead of skipping.
    """
    from scheduler import job_weekly_report
    background_tasks.add_task(job_weekly_report, overwrite)
    return {"status": "ok", "overwrite": overwrite, "note": "Weekly report job queued in background"}


@app.get("/")
async def root():
    return {"message": "Agency Tracker running. See /health for status."}
