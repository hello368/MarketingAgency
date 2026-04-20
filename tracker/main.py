"""
Agency Remote Tracking System — Phase 1 Main Server
FastAPI webhook receiver for Google Chat events.

Endpoints:
  POST /webhook/google-chat   → Google Chat Bot events
  GET  /health                → Status check
"""
import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, BackgroundTasks
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
)
from gsheet_handler import GSheetHandler
import ai_engine
import state
import task_tracker
import gchat_sender

# ─────────────────────────────────────────
# Startup
# ─────────────────────────────────────────
TZ = ZoneInfo(TIMEZONE)
db.init_db()

app = FastAPI(title="Agency Remote Tracking System", version="1.0.0")
scheduler = create_scheduler()
scheduler.start()
telegram_bot.start_polling()

log.info("Agency Tracker started — timezone=%s", TIMEZONE)

_ai_ok: bool = False


@app.on_event("startup")
async def startup_ai_engine():
    """Initialise ModelRouter (OpenRouter) — smart routing for SLA + alerts."""
    global _ai_ok
    _ai_ok = ai_engine.init()


@app.on_event("startup")
async def startup_gsheet_test():
    """Connect to Google Sheets and mark Ivan Online as a connectivity test."""
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json")
    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID", "1e_YQ9YBC_SCfM3Ex_rkg_f5NWlr5nOF9LBk3GT2TwZ8"
    )

    if not spreadsheet_id:
        log.error("[GSheet] ❌ SPREADSHEET_ID is not set — Sheets integration disabled.")
        return

    try:
        state.gsheet = GSheetHandler(credentials_path=creds_path, spreadsheet_id=spreadsheet_id)
        updated = state.gsheet.update_status("Ivan", "🟢 Online (System Initialized)")
        if updated:
            log.info("[GSheet] ✅ Connectivity test passed — Ivan's status updated on the sheet.")
            state.gsheet_ok = True
        else:
            log.warning("[GSheet] ⚠️ Connected but 'Ivan' was not found in the Live Status tab.")
    except FileNotFoundError as e:
        log.error("[GSheet] ❌ %s", e)
    except Exception as e:
        log.error("[GSheet] ❌ Startup connection failed: %s", e)


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

    # ── MESSAGE — return 200 immediately, process in background
    if event_type == "MESSAGE":
        background_tasks.add_task(_handle_message, norm_body)
        return {}

    return {}


def _handle_message(body: dict) -> None:
    """
    Sync handler — runs AFTER the 200 response is sent.

    Dispatch order (strict priority):
      1. Task Tracker  — @best commands and task completions  [FIRST — early return]
      2. Check-in      — 09:00 daily goals in Daily Report thread
      3. EOD           — 16:45 result links in Daily Report thread
      4. SLA           — resolve existing timers + start new ones for @mentions
    """
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

    matched_name = _resolve_member_name(sender_display)
    if matched_name and sender_google_id:
        db.upsert_google_chat_id(matched_name, sender_google_id, sender_display)

    # ── Task Nag: cancel timer if the assignee sends any reply in the task thread
    if thread_key and matched_name:
        task_tracker.acknowledge_nag_timers(thread_key, matched_name)

    # ── [1] TASK TRACKER — highest priority, checked before everything else
    task_reply = task_tracker.process_task_message(body)
    if task_reply:
        if space_name and thread_key:
            gchat_sender.reply_to_thread(space_name, thread_key, task_reply["text"])
        return

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


@app.get("/")
async def root():
    return {"message": "Agency Tracker running. See /health for status."}
