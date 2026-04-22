"""
V2 command router. Owns ONLY these verbs:
- @best update <task_id> <status>
- @best ask <question>
- @best brief
- @best undo
- @best check

All other @best messages (including @best @Person task assignments)
fall through to task_tracker.process_task_message().
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread

from config import TIMEZONE
from llm_client import summarize_update, answer_rag, generate_brief
from sheets_client import _get_or_create_tab

# ── Wiki singletons — lazily initialised on first @best wiki call ─────────────
_wiki_store  = None
_wiki_router = None


def _get_wiki_store():
    """Return (WikiStore, WikiModelRouter) singletons, initialising on first call."""
    global _wiki_store, _wiki_router
    if _wiki_store is None:
        try:
            from wiki.store import WikiStore
            from wiki.interceptor import WikiModelRouter
            _wiki_router = WikiModelRouter()
            _wiki_store  = WikiStore()
            _wiki_store.set_router(_wiki_router)
            log.info("[Router] WikiStore initialised")
        except Exception as exc:
            log.error("[Router] WikiStore init failed: %s", exc)
            return None, None
    return _wiki_store, _wiki_router

log = logging.getLogger(__name__)
TZ  = ZoneInfo(TIMEZONE)

BOT_NAME = os.getenv("BOT_DISPLAY_NAME", "best").lower()

# ── Sheet tab definitions ─────────────────────────────────────────────────────
TAB_UPDATES = "Update_Tracker"
TAB_TASKS   = "Task_Tracker"

_UPD_HEADERS = [
    "날짜/시간", "스페이스 이름", "스페이스 ID",
    "⚡ 5단어 요약", "원본 내용", "🔍 상세 컨텍스트", "미디어 링크",
]
_TASK_HEADERS = [
    "날짜/시간", "우선순위", "담당자", "업무 내용", "미디어 링크", "상태",
]


def provision_tabs() -> None:
    """Pre-create both sheet tabs on server startup if they don't exist."""
    try:
        _get_or_create_tab(TAB_UPDATES, _UPD_HEADERS)
        _get_or_create_tab(TAB_TASKS,   _TASK_HEADERS)
        log.info("[Router] Update_Tracker / Task_Tracker tabs ready")
    except Exception as exc:
        log.warning("[Router] provision_tabs failed (continuing): %s", exc)


# ── Media/URL extraction ──────────────────────────────────────────────────────
_URL_RE = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)


def _extract_media(message: dict) -> str:
    """Extract attachments + in-body URLs from a GChat message, joined with semicolons."""
    links: list[str] = []

    for att in (message.get("attachment") or message.get("attachments") or []):
        drv_id = att.get("driveDataRef", {}).get("driveFileId", "")
        dl_uri = att.get("downloadUri", "")
        if drv_id:
            links.append(f"https://drive.google.com/file/d/{drv_id}/view")
        elif dl_uri:
            links.append(dl_uri)

    text = message.get("text", "")
    for url in _URL_RE.findall(text):
        if url not in links:
            links.append(url)

    return "; ".join(links)


# ── Sheet read/write helpers ──────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _append_update(ts: str, space_name: str, space_id: str,
                   summary: str, content: str, context: str, media: str) -> int:
    ws = _get_or_create_tab(TAB_UPDATES, _UPD_HEADERS)
    ws.append_row(
        [ts, space_name, space_id, summary, content, context, media],
        value_input_option="USER_ENTERED",
    )
    return len(ws.get_all_values())


def _get_updates_by_space(space_id: str) -> list[dict]:
    """Return all Update_Tracker rows for the given space ID."""
    try:
        ws   = _get_or_create_tab(TAB_UPDATES, _UPD_HEADERS)
        rows = ws.get_all_records(expected_headers=list(_UPD_HEADERS))
        return [
            {
                "timestamp":        r.get("날짜/시간", ""),
                "summary":          r.get("⚡ 5단어 요약", ""),
                "context":          r.get("🔍 상세 컨텍스트", ""),
                "original_content": r.get("원본 내용", ""),
                "space_name":       r.get("스페이스 이름", ""),
            }
            for r in rows
            if str(r.get("스페이스 ID", "")).strip() == space_id.strip()
        ]
    except Exception as exc:
        log.warning("[Router] get_updates_by_space failed: %s", exc)
        return []


def _get_recent_records(today: str, yesterday: str) -> tuple[list[dict], list[dict]]:
    """Return today's/yesterday's Update_Tracker + Task_Tracker records."""
    updates: list[dict] = []
    tasks:   list[dict] = []

    try:
        ws = _get_or_create_tab(TAB_UPDATES, _UPD_HEADERS)
        for r in ws.get_all_records(expected_headers=list(_UPD_HEADERS)):
            if str(r.get("날짜/시간", ""))[:10] in (today, yesterday):
                updates.append({
                    "timestamp":  r.get("날짜/시간", ""),
                    "space_name": r.get("스페이스 이름", ""),
                    "summary":    r.get("⚡ 5단어 요약", ""),
                    "context":    r.get("🔍 상세 컨텍스트", ""),
                })
    except Exception as exc:
        log.warning("[Router] brief update fetch failed: %s", exc)

    try:
        ws = _get_or_create_tab(TAB_TASKS, _TASK_HEADERS)
        for r in ws.get_all_records(expected_headers=list(_TASK_HEADERS)):
            if str(r.get("날짜/시간", ""))[:10] in (today, yesterday):
                tasks.append({
                    "timestamp": r.get("날짜/시간", ""),
                    "priority":  r.get("우선순위", ""),
                    "assignee":  r.get("담당자", ""),
                    "content":   r.get("업무 내용", ""),
                    "status":    r.get("상태", ""),
                })
    except Exception as exc:
        log.warning("[Router] brief task fetch failed: %s", exc)

    return updates, tasks


def _delete_last_row() -> str:
    """Delete the most recent row from whichever of Update_Tracker / Task_Tracker is newer."""
    best_tab: str | None                = None
    best_ws:  gspread.Worksheet | None  = None
    best_ts                             = ""
    best_idx                            = 0

    for tab, headers in [(TAB_UPDATES, _UPD_HEADERS), (TAB_TASKS, _TASK_HEADERS)]:
        try:
            ws   = _get_or_create_tab(tab, headers)
            vals = ws.get_all_values()
            if len(vals) < 2:      # header row only
                continue
            ts = str(vals[-1][0])
            if ts > best_ts:
                best_ts  = ts
                best_tab = tab
                best_ws  = ws
                best_idx = len(vals)
        except Exception as exc:
            log.warning("[Router] undo tab fetch failed (%s): %s", tab, exc)

    if best_ws and best_idx > 1:
        best_ws.delete_rows(best_idx)
        return f"✅ [{best_tab}] Last entry (row {best_idx}) deleted."
    return "⚠️ No entry found to delete."


def _get_tasks_for(assignee_raw: str) -> list[dict]:
    """Return active tasks assigned to the given person."""
    try:
        ws   = _get_or_create_tab(TAB_TASKS, _TASK_HEADERS)
        rows = ws.get_all_records(expected_headers=list(_TASK_HEADERS))
        name = assignee_raw.lstrip("@").strip().lower()
        return [
            r for r in rows
            if name in str(r.get("담당자", "")).lower()
            and str(r.get("상태", "")).replace(" ", "") in ("진행중",)
        ]
    except Exception as exc:
        log.warning("[Router] get_tasks_for failed: %s", exc)
        return []


def _get_active_assignees() -> list[str]:
    """Return sorted unique assignee names that have active tasks."""
    try:
        ws   = _get_or_create_tab(TAB_TASKS, _TASK_HEADERS)
        rows = ws.get_all_records(expected_headers=list(_TASK_HEADERS))
        seen: set[str] = set()
        names: list[str] = []
        for r in rows:
            if str(r.get("상태", "")).replace(" ", "") in ("진행중",):
                name = str(r.get("담당자", "")).strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
        return sorted(names)
    except Exception as exc:
        log.warning("[Router] get_active_assignees failed: %s", exc)
        return []


# ── Text parsing helpers ──────────────────────────────────────────────────────
_BOT_MENTION_RE = re.compile(
    r'@' + re.escape(BOT_NAME) + r'\b', re.IGNORECASE
)


def _strip_bot_mention(text: str) -> str:
    return _BOT_MENTION_RE.sub("", text).strip()


def _human_mentions(message: dict) -> list[str]:
    """Return non-bot display names from message annotations."""
    names: list[str] = []
    for ann in message.get("annotations", []):
        if ann.get("type") == "USER_MENTION":
            user = ann.get("userMention", {}).get("user", {})
            if user.get("type") != "BOT":
                display = user.get("displayName", "").strip()
                if display and display.lower() != BOT_NAME:
                    names.append(display)
    return names


def _names_from_text(text: str) -> list[str]:
    """Extract @Name mentions from text (fallback when annotations are absent)."""
    return [m.lstrip("@") for m in re.findall(r'@(\S+)', text)]


# ── Main dispatcher ───────────────────────────────────────────────────────────

def dispatch(body: dict) -> str | None:
    """
    Parse a @best message, execute the matched V2 command, and return a response string.
    Returns None if the message is not a recognised command.
    """
    message       = body.get("message", {})
    text          = message.get("text", "").strip()
    space         = message.get("space", {})
    space_id      = space.get("name", "")           # "spaces/XXXXXXX"
    space_display = space.get("displayName", "")

    # Ignore messages that don't mention @best
    if not _BOT_MENTION_RE.search(text):
        return None

    remainder = _strip_bot_mention(text).strip()
    lower     = remainder.lower()
    media     = _extract_media(message)
    ts        = _now_str()

    # ── 1. update ────────────────────────────────────────────────────────────
    if lower.startswith("update"):
        content = remainder[len("update"):].strip()
        if not content and not media:
            return "⚠️ Please include update content or a file attachment."
        try:
            if content:
                summary, context = summarize_update(content)
            else:
                summary, context = "(attachment update)", "(media file attached)"
            _append_update(ts, space_display, space_id, summary, content, context, media)
        except Exception as exc:
            log.error("[Router] update error: %s", exc)
            return "⚠️ Failed to log update. Please try again."
        return "Update logged to sheet! ✅"

    # ── 2. ask ────────────────────────────────────────────────────────────────
    if lower.startswith("ask"):
        question = remainder[len("ask"):].strip()
        if not question:
            return "❓ Please include your question. e.g. `@best ask What did Luna work on last week?`"
        try:
            rows   = _get_updates_by_space(space_id)
            answer = answer_rag(question, rows)
        except Exception as exc:
            log.error("[Router] ask error: %s", exc)
            return "⚠️ Failed to process question. Please try again."
        return f"🔍 *Question:* {question}\n\n{answer}"

    # ── 3. brief ─────────────────────────────────────────────────────────────
    if lower.startswith("brief"):
        try:
            now       = datetime.now(TZ)
            today     = now.strftime("%Y-%m-%d")
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            updates, tasks = _get_recent_records(today, yesterday)
            brief = generate_brief(updates, tasks)
        except Exception as exc:
            log.error("[Router] brief error: %s", exc)
            return "⚠️ Failed to generate briefing. Please try again."
        return brief

    # ── 4. undo ──────────────────────────────────────────────────────────────
    if lower.startswith("undo"):
        try:
            return _delete_last_row()
        except Exception as exc:
            log.error("[Router] undo error: %s", exc)
            return "⚠️ Failed to delete entry. Please try again."

    # ── 5. check ─────────────────────────────────────────────────────────────
    if lower.startswith("check"):
        after     = remainder[len("check"):].strip()
        assignees = (
            _human_mentions(message)
            or _names_from_text(after)
            or ([after.lstrip("@").strip()] if after.strip() else [])
        )
        if not assignees:
            return "❓ Usage: `@best check Ivan` or `@best check @Ivan`"
        try:
            all_tasks: list[dict] = []
            for name in assignees:
                all_tasks.extend(_get_tasks_for(name))
        except Exception as exc:
            log.error("[Router] check error: %s", exc)
            return "⚠️ Failed to retrieve tasks. Please try again."

        names_str = ", ".join(assignees)
        if not all_tasks:
            return f"✅ No active tasks for *{names_str}*."

        lines = [f"📋 Active tasks for *{names_str}*:\n"]
        for i, t in enumerate(all_tasks, 1):
            badge   = " 🔥" if "긴급" in str(t.get("우선순위", "")) else ""
            date_str = str(t.get("날짜/시간", ""))[:10]
            lines.append(
                f"{i}. {t.get('업무 내용', '')}{badge} _(added: {date_str})_"
            )
        return "\n".join(lines)

    # ── 6. wiki ──────────────────────────────────────────────────────────────
    if lower.startswith("wiki"):
        query = remainder[len("wiki"):].strip()
        if not query:
            return (
                "📚 *Wiki Knowledge Base*\n"
                "`@best wiki [client name]` — look up a client's summary and data\n"
                "Example: `@best wiki Luna Spa`"
            )
        store, _ = _get_wiki_store()
        if store is None:
            return "⚠️ Wiki is unavailable. Check OPENROUTER_API_KEY and Google credentials."
        try:
            result = store.query_client(query)
        except Exception as exc:
            log.error("[Router] wiki query error: %s", exc)
            return "⚠️ Wiki query failed. Please try again."
        return result.format_reply()

    # falls through to task_tracker
    return None
