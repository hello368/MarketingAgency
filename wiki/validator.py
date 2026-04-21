"""
Wiki Verification Loop — Interactive card UI for entity confirmation.

When WikiInterceptor detects business entities, this module posts a
Google Chat Card v2 to the originating thread. The card shows the parsed
data and has three action buttons:

  [✅ Confirm]  → writes all detected fields to Client_Wiki sheet as "Verified"
  [✏️ Edit]     → prompts the user to type corrected values in the thread
  [❌ Cancel]   → discards the pending entry silently

Action flow
-----------
1. tracker/main.py calls validator.prompt_verification(extraction, ...) after
   entity detection → a card is posted in the originating thread.
2. User clicks a button → Google Chat sends a CARD_CLICKED event to the
   webhook → tracker/main.py calls validator.handle_card_click(body) and
   returns the result as the HTTP response (must be synchronous).
3. If Edit was clicked, tracker/main.py calls validator.is_awaiting_edit()
   on every subsequent message in that thread and routes to
   validator.handle_edit_reply() when True.

Pending state is persisted in SQLite (wiki_pending / wiki_awaiting_edit).
State survives server restarts; entries older than 24 h are cleaned up by
wiki.db.cleanup_expired_pending().
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from wiki import db as _wdb

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# WikiValidator
# ─────────────────────────────────────────────────────────────────────────────

class WikiValidator:
    """
    Manages the verification card lifecycle for extracted wiki entities.

    Parameters
    ----------
    gchat : module
        gchat_sender module — must expose send_card_to_thread() and
        reply_to_thread().
    sheets : module
        sheets_client module — must expose log_wiki_entry().
    tz : ZoneInfo, optional
        Timezone for timestamps (default: Asia/Manila).
    """

    def __init__(self, gchat, sheets, tz: ZoneInfo | None = None):
        self._gchat  = gchat
        self._sheets = sheets
        self._tz     = tz or ZoneInfo("Asia/Manila")

    # ── Public API ────────────────────────────────────────────────────────────

    def prompt_verification(
        self,
        extraction,           # WikiExtraction instance
        space_name: str,      # "spaces/XXXXXXXX"
        thread_key: str,      # "spaces/XXXXXXXX/threads/YYYYYYYY"
        sender_google_id: str,
        source_link: str = "",
    ) -> str | None:
        """
        Post a verification card to the originating thread.

        Returns the pending_id if a card was sent, None if the extraction
        contains no detected fields.
        """
        if not extraction.has_any():
            return None

        pending_id = _make_id(thread_key)
        entry = {
            "pending_id":        pending_id,
            "extraction":        extraction.to_dict(),
            "space_name":        space_name,
            "thread_key":        thread_key,
            "sender_google_id":  sender_google_id,
            "source_link":       source_link,
            "created_at":        datetime.now(self._tz).isoformat(),
        }

        _wdb.save_pending(pending_id, entry, user_id=sender_google_id)

        card = _build_verification_card(pending_id, extraction)
        self._gchat.send_card_to_thread(space_name, thread_key, card)
        log.info(
            "[WikiValidator] Card posted — pending_id=%s thread=%s status=%s",
            pending_id, thread_key, extraction.status,
        )
        return pending_id

    def handle_card_click(self, body: dict) -> dict:
        """
        Handle a CARD_CLICKED event payload from Google Chat.

        Returns a dict suitable as the HTTP response body. Google Chat
        reads this synchronously — must be returned within 30 s.
        """
        action     = body.get("action", {})
        method     = action.get("function") or action.get("actionMethodName", "")
        parameters = {p["key"]: p["value"] for p in action.get("parameters", [])}
        pending_id = parameters.get("pending_id", "")

        log.info("[WikiValidator] CARD_CLICKED method=%r pending_id=%r", method, pending_id)

        if not pending_id:
            log.warning("[WikiValidator] CARD_CLICKED missing pending_id — ignored")
            return {"text": "⚠️ Could not identify the pending verification."}

        entry = _wdb.get_pending(pending_id)

        if not entry:
            return {"text": "⚠️ This verification card has already been handled or has expired."}

        if method == "wiki_confirm":
            return self._do_confirm(pending_id, entry)
        if method == "wiki_edit":
            return self._do_edit_prompt(pending_id, entry)
        if method == "wiki_cancel":
            return self._do_cancel(pending_id, entry)

        log.warning("[WikiValidator] Unknown action method: %r", method)
        return {"text": "⚠️ Unknown action."}

    def handle_edit_reply(self, thread_key: str, new_text: str) -> bool:
        """
        Called when a user sends a follow-up message in a thread that is
        awaiting an edit correction.

        Returns True if the message was consumed by the edit flow, False
        if the thread is not waiting for an edit (caller should continue
        normal processing).
        """
        pending_id = _wdb.get_awaiting_edit(thread_key)
        if not pending_id:
            return False

        entry = _wdb.get_pending(pending_id)
        if not entry:
            # Stale awaiting state — clean up
            _wdb.delete_awaiting_edit(thread_key)
            return False

        original_entities = entry["extraction"]["entities"]
        updated_entities  = _parse_edit_text(new_text, original_entities)

        self._write_to_sheet(entry, updated_entities, status="Verified")

        self._gchat.reply_to_thread(
            entry["space_name"],
            entry["thread_key"],
            "✅ Entry updated and saved to the Knowledge Base as *Verified*.",
        )

        _wdb.delete_pending(pending_id)
        _wdb.delete_awaiting_edit(thread_key)

        log.info("[WikiValidator] Edit reply processed — pending_id=%s", pending_id)
        return True

    def is_awaiting_edit(self, thread_key: str) -> bool:
        """True if this thread is currently waiting for an edit correction."""
        return _wdb.get_awaiting_edit(thread_key) is not None

    # ── Private action handlers ───────────────────────────────────────────────

    def _do_confirm(self, pending_id: str, entry: dict) -> dict:
        entities = entry["extraction"]["entities"]
        self._write_to_sheet(entry, entities, status="Verified")

        _wdb.delete_pending(pending_id)

        log.info("[WikiValidator] Confirmed — pending_id=%s", pending_id)
        return {"text": "✅ Entry confirmed and saved to the Knowledge Base as *Verified*."}

    def _do_edit_prompt(self, pending_id: str, entry: dict) -> dict:
        thread_key = entry["thread_key"]
        _wdb.set_awaiting_edit(thread_key, pending_id)

        log.info(
            "[WikiValidator] Edit prompted — pending_id=%s thread=%s",
            pending_id, thread_key,
        )
        return {
            "text": (
                "✏️ Please type your corrections in this thread.\n"
                "Use `field: value` format, one per line (omit unchanged fields):\n\n"
                "`client: Luna Spa`\n"
                "`budget: 2500`\n"
                "`service_fee: 1000`\n"
                "`project_name: Q2 Campaign`\n"
                "`assignee: Tiffany`"
            )
        }

    def _do_cancel(self, pending_id: str, entry: dict) -> dict:
        thread_key = entry.get("thread_key", "")
        _wdb.delete_pending(pending_id)
        if thread_key:
            _wdb.delete_awaiting_edit(thread_key)

        log.info("[WikiValidator] Cancelled — pending_id=%s", pending_id)
        return {"text": "❌ Verification cancelled. No data was saved."}

    # ── Sheet write ───────────────────────────────────────────────────────────

    def _write_to_sheet(self, entry: dict, entities: dict, status: str) -> None:
        """
        Write each detected entity as a separate row in the Client_Wiki tab.

        Row schema: Timestamp | Client | Category | Value | Source_Link | Status
        """
        timestamp   = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M")
        source_link = entry.get("source_link", "")

        cn_data = entities.get("client_name", {})
        client_value = (
            cn_data.get("value", "") if isinstance(cn_data, dict) else (cn_data or "")
        )

        for field_name, field_data in entities.items():
            if isinstance(field_data, dict):
                value = field_data.get("value") or ""
            else:
                value = str(field_data) if field_data else ""

            if not value:
                continue

            client   = value if field_name == "client_name" else client_value
            category = field_name.replace("_", " ").title()

            try:
                self._sheets.log_wiki_entry(
                    timestamp, client, category, value, source_link, status
                )
                log.info(
                    "[WikiValidator] Sheet row written — %s=%r client=%r status=%s",
                    field_name, value, client, status,
                )
            except Exception as exc:
                log.error(
                    "[WikiValidator] Sheet write failed for field=%s: %s",
                    field_name, exc,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: unique pending ID
# ─────────────────────────────────────────────────────────────────────────────

def _make_id(thread_key: str) -> str:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    tail = re.sub(r"[^A-Za-z0-9]", "_", (thread_key or ""))[-10:]
    return f"wiki_{tail}_{ts}"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse edit reply text
# ─────────────────────────────────────────────────────────────────────────────

_EDIT_FIELD_RE = re.compile(
    r"^(client(?:_name)?|budget|service[_ ]fee|project(?:_name)?|assignee)"
    r"\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

_KEY_NORMALISE = {
    "client":       "client_name",
    "client_name":  "client_name",
    "budget":       "budget",
    "service_fee":  "service_fee",
    "service fee":  "service_fee",
    "project":      "project_name",
    "project_name": "project_name",
    "assignee":     "assignee",
}


def _parse_edit_text(text: str, original_entities: dict) -> dict:
    """
    Parse `field: value` lines from an edit reply.

    If no structured lines are found, the original entities are returned
    unchanged (no silent overwrites).
    """
    result: dict = {}
    for k, v in original_entities.items():
        result[k] = dict(v) if isinstance(v, dict) else {"value": v, "raw": v, "confidence": 1.0}

    matches = _EDIT_FIELD_RE.findall(text)
    if not matches:
        return result

    for raw_key, raw_value in matches:
        normalised = raw_key.lower().replace(" ", "_")
        canonical  = _KEY_NORMALISE.get(normalised, normalised)
        if canonical in result:
            cleaned = raw_value.strip()
            result[canonical] = {"value": cleaned, "raw": cleaned, "confidence": 1.0}
            log.debug("[WikiValidator] Edit parsed: %s → %r", canonical, cleaned)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build Google Chat Card v2
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_LABELS = {
    "client_name":  "Client Name",
    "budget":       "Budget",
    "service_fee":  "Service Fee",
    "project_name": "Project Name",
    "assignee":     "Assignee",
}


def _build_verification_card(pending_id: str, extraction) -> dict:
    """
    Return a cardsV2 payload for the Google Chat REST API.

    The card has:
      • A header identifying it as a wiki entity detection result.
      • One decoratedText widget per detected field showing value + confidence.
      • A warning row if the extraction has a pending reason.
      • A buttonList row with [✅ Confirm] [✏️ Edit] [❌ Cancel].
    """
    widgets: list[dict] = []

    for field_name, label in _FIELD_LABELS.items():
        f = extraction.extracted_fields().get(field_name)
        if f is None or not f.has_value():
            continue

        conf_pct = int(f.confidence * 100)
        widgets.append({
            "decoratedText": {
                "topLabel":    label,
                "text":        f"<b>{f.value}</b>",
                "bottomLabel": f'Confidence: {conf_pct}% | Raw: "{f.raw}"',
                "startIcon":   {"knownIcon": "DESCRIPTION"},
            }
        })

    if extraction.pending_reason:
        widgets.append({
            "textParagraph": {
                "text": f'⚠️ <b>Pending reason:</b> {extraction.pending_reason}'
            }
        })

    status_icon = "🟡 Pending" if extraction.status == "Pending" else "🟢 Ready to Verify"
    widgets.append({
        "textParagraph": {"text": f"Status: <b>{status_icon}</b>"}
    })

    # ── Action buttons ────────────────────────────────────────────────────────
    def _btn(label: str, fn: str) -> dict:
        return {
            "text": label,
            "onClick": {
                "action": {
                    "function":   fn,
                    "parameters": [{"key": "pending_id", "value": pending_id}],
                }
            },
        }

    widgets.append({
        "buttonList": {
            "buttons": [
                _btn("✅ Confirm", "wiki_confirm"),
                _btn("✏️ Edit",    "wiki_edit"),
                _btn("❌ Cancel",  "wiki_cancel"),
            ]
        }
    })

    return {
        "cardsV2": [
            {
                "cardId": f"wiki_verify_{pending_id}",
                "card": {
                    "header": {
                        "title":    "📚 Wiki Entity Detected",
                        "subtitle": "Please verify the extracted data before saving to the Knowledge Base.",
                        "imageType": "SQUARE",
                    },
                    "sections": [
                        {
                            "header":      "Extracted Fields",
                            "collapsible": False,
                            "widgets":     widgets,
                        }
                    ],
                },
            }
        ]
    }
