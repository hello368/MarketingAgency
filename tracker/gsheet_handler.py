"""
MARTS — Google Sheet Handler
Manages all read/write operations against the Master Google Sheet.

Responsibilities:
  - Connect via Service Account credentials.json
  - Auto-create "Live Status" tab with all team members pre-populated
  - update_status(name, status) → updates a member's row in Live Status
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

# ── Scopes required for Sheets + Drive
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# ── Timezone for timestamps
_TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Manila"))

# ── All 12 team members (pre-populated into Live Status tab)
TEAM_MEMBERS = [
    ("Michael",  "Top Leader"),
    ("Kaye",     "Management & Ops"),
    ("Anna",     "Management & Ops"),
    ("Ivan",     "Tech & Dev"),
    ("Izzy",     "Tech & Dev"),
    ("Kevin",    "Tech & Dev"),
    ("Milo",     "Tech & Dev"),
    ("Tiffany",  "Ads & Growth"),
    ("Danni",    "Ads & Growth"),
    ("Silver",   "Creative"),
    ("Jhon",     "Creative"),
    ("Lovely",   "Sales Support"),
]

# ── Live Status tab layout (matches actual sheet structure)
# A = Name (이름)
# B = Current Status (현재 상태)   ← update_status() writes here
# C = Last Active (최근 활동)      ← timestamp written here
# D = Telegram Pings (호출 횟수)
# E = Notes (특이사항)
TAB_NAME    = "Live Status"
COL_NAME    = 1   # A
COL_STATUS  = 2   # B
COL_UPDATED = 3   # C


class GSheetHandler:
    """
    Single-instance handler for the MARTS Master Google Sheet.
    Raises a clear error on startup if credentials or sheet ID are wrong.
    """

    def __init__(self, credentials_path: str, spreadsheet_id: str):
        self._spreadsheet_id = spreadsheet_id
        self._ws: gspread.Worksheet | None = None

        log.info("[GSheet] Connecting with credentials: %s", credentials_path)
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(
                f"credentials.json not found at: {credentials_path}\n"
                f"Please place your Google Service Account JSON at that path."
            )

        creds = Credentials.from_service_account_file(credentials_path, scopes=_SCOPES)
        self._client = gspread.authorize(creds)

        log.info("[GSheet] Opening spreadsheet: %s", spreadsheet_id)
        self._spreadsheet = self._client.open_by_key(spreadsheet_id)

        self._ws = self._ensure_live_status_tab()
        log.info("[GSheet] ✅ Connected. Live Status tab ready (%d rows).", len(TEAM_MEMBERS) + 1)

    # ──────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────

    def _ensure_live_status_tab(self) -> gspread.Worksheet:
        """
        Return the 'Live Status' worksheet.
        Creates it with headers + all 12 members if it does not exist.
        """
        try:
            ws = self._spreadsheet.worksheet(TAB_NAME)
            log.info("[GSheet] 'Live Status' tab found.")
        except gspread.WorksheetNotFound:
            log.info("[GSheet] 'Live Status' tab not found — creating...")
            ws = self._spreadsheet.add_worksheet(
                title=TAB_NAME, rows=20, cols=4
            )
            # Header row (bold + blue background)
            ws.append_row(HEADERS, value_input_option="USER_ENTERED")
            ws.format(
                "A1:D1",
                {
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "backgroundColor": {"red": 0.12, "green": 0.34, "blue": 0.6},
                },
            )
            # Pre-populate all team members
            now_str = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
            rows = [[name, group, "⚪ Offline", now_str] for name, group in TEAM_MEMBERS]
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            log.info("[GSheet] 'Live Status' tab created with %d members.", len(TEAM_MEMBERS))

        return ws

    def _find_row(self, name: str) -> int | None:
        """
        Find the 1-indexed row number for a given team member name.
        Returns None if not found.
        """
        # get_all_values() is one API call — efficient
        all_values = self._ws.get_all_values()
        name_lower = name.strip().lower()
        for i, row in enumerate(all_values, start=1):
            if row and row[0].strip().lower() == name_lower:
                return i
        return None

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def update_status(self, name: str, status: str) -> bool:
        """
        Find `name` in the Live Status tab and update their Status + Last Updated.

        Args:
            name:   Team member's first name (case-insensitive).
            status: Status string, e.g. "🟢 Online", "🔴 Offline", "🟡 Away".

        Returns:
            True if the row was found and updated.
            False if the name was not found in the sheet.
        """
        row_idx = self._find_row(name)
        if row_idx is None:
            log.warning("[GSheet] update_status: '%s' not found in Live Status tab.", name)
            return False

        now_str = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")

        # gspread 6.x: values first, range_name second
        self._ws.update(
            [[status, now_str]],
            f"B{row_idx}:C{row_idx}",
            value_input_option="USER_ENTERED",
        )
        log.info("[GSheet] ✅ %s → '%s' at %s", name, status, now_str)
        return True

    def get_all_statuses(self) -> list[dict]:
        """Return all member statuses as a list of dicts."""
        records = self._ws.get_all_records()
        return records

    def bulk_reset(self, status: str = "⚪ Offline"):
        """Reset every member's status (e.g., at end of day)."""
        now_str = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
        all_values = self._ws.get_all_values()
        for i, row in enumerate(all_values, start=1):
            if i == 1:  # skip header
                continue
            if row and row[0].strip():
                self._ws.update([[status, now_str]], f"B{i}:C{i}")
        log.info("[GSheet] Bulk reset → '%s'", status)

    # ──────────────────────────────────────────────────────────
    # SLA Log tab
    # ──────────────────────────────────────────────────────────

    _SLA_TAB   = "SLA Log"
    _SLA_HEADS = ["Date/Time", "Tagged By", "Tagged Person", "Space Name", "Resolved?", "Telegram Alert Sent"]

    def _ensure_sla_tab(self) -> "gspread.Worksheet":
        try:
            ws = self._spreadsheet.worksheet(self._SLA_TAB)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=self._SLA_TAB, rows=500, cols=6)
            ws.append_row(self._SLA_HEADS, value_input_option="USER_ENTERED")
            ws.format("A1:F1", {
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor": {"red": 0.75, "green": 0.09, "blue": 0.09},
            })
            log.info("[GSheet] Created '%s' tab.", self._SLA_TAB)
        return ws

    def log_sla_breach(
        self,
        tagger: str,
        tagged: str,
        space: str,
        resolved: bool,
        alert_sent: bool,
    ) -> None:
        """Append one row to the SLA Log tab."""
        try:
            ws = self._ensure_sla_tab()
            now_str = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
            row = [
                now_str,
                tagger,
                tagged,
                space,
                "✅ Yes" if resolved else "❌ No",
                "✅ Sent" if alert_sent else "❌ Not Sent",
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            log.info("[GSheet] SLA breach logged — %s tagged %s in '%s' resolved=%s",
                     tagger, tagged, space, resolved)
        except Exception as e:
            log.error("[GSheet] Failed to log SLA breach: %s", e)
