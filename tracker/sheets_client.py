"""
Google Sheets — Master Dashboard writer.
Tabs: Daily Check-ins | EOD Results | SLA Ping Log | Ping Summary
"""
import os
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials
from config import TIMEZONE

log = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
_CREDS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json")
_SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# Tab names
TAB_CHECKIN      = "Daily Check-ins"
TAB_EOD          = "EOD Results"
TAB_SLA_LOG      = "SLA Ping Log"
TAB_SUMMARY      = "Ping Summary"
TAB_CLIENT_WIKI  = "Client_Wiki"
TAB_CHAT_ARCHIVE = "Chat_Archive"
TAB_WEEKLY_KPI   = "Weekly_KPI"

_CHECKIN_HEADERS  = ["Date", "Name", "Group", "Check-in Time", "Goal 1", "Goal 2", "Goal 3", "Status"]
_EOD_HEADERS      = ["Date", "Name", "Group", "Submit Time", "Link 1", "Link 2", "Link 3", "Status"]
_SLA_HEADERS      = ["Date", "Time", "Space", "Tagger", "Tagged", "Thread", "15-Min Met?", "Telegram Pings"]
_SUMMARY_HEADERS  = ["Name", "Group", "Pings This Week", "Pings This Month", "Total Pings", "Last Breach"]
_WIKI_HEADERS     = ["Timestamp", "Client", "Category", "Value", "Source_Link", "Status"]
_ARCHIVE_HEADERS  = ["Timestamp", "Space", "User", "Message", "Link", "Summary"]
_WEEKLY_KPI_HEADERS = [
    "Week_Label", "Date_From", "Date_To",
    "Checkins_OnTime", "Checkins_Late", "Checkins_Missing",
    "EODs_Submitted", "EODs_Missing",
    "SLA_Breaches", "SLA_Met", "SLA_Breach_Rate_Pct",
    "Tasks_Created", "Tasks_Completed",
    "Generated_At",
]

_client: gspread.Client | None = None
_sheet: gspread.Spreadsheet | None = None
_lock = threading.RLock()


def _get_sheet() -> gspread.Spreadsheet:
    global _client, _sheet
    with _lock:
        if _sheet is None:
            creds = Credentials.from_service_account_file(_CREDS_PATH, scopes=_SCOPES)
            _client = gspread.authorize(creds)
            _sheet = _client.open_by_key(_SPREADSHEET_ID)
            _ensure_tabs()
    return _sheet


def _get_or_create_tab(title: str, headers: list[str]) -> gspread.Worksheet:
    sheet = _get_sheet()
    try:
        ws = sheet.worksheet(title)
        return ws
    except gspread.WorksheetNotFound:
        pass

    print(f"[SHEETS] Creating tab: {title}...", flush=True)
    log.info("[SHEETS] Creating tab: %s", title)
    try:
        ws = sheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="USER_ENTERED")
        ws.format("1:1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.6}})
        print(f"[SHEETS] ✅ Tab created successfully: {title}", flush=True)
        log.info("[SHEETS] ✅ Tab created successfully: %s", title)
        return ws
    except Exception as creation_err:
        print(f"[SHEETS] ❌ Failed to create tab '{title}': {creation_err}", flush=True)
        log.error("[SHEETS] ❌ Failed to create tab '%s': %s", title, creation_err)
        raise


def _ensure_tabs():
    _get_or_create_tab(TAB_CHECKIN,      _CHECKIN_HEADERS)
    _get_or_create_tab(TAB_EOD,          _EOD_HEADERS)
    _get_or_create_tab(TAB_SLA_LOG,      _SLA_HEADERS)
    _get_or_create_tab(TAB_SUMMARY,      _SUMMARY_HEADERS)
    _get_or_create_tab(TAB_CLIENT_WIKI,  _WIKI_HEADERS)
    _get_or_create_tab(TAB_CHAT_ARCHIVE, _ARCHIVE_HEADERS)
    _get_or_create_tab(TAB_WEEKLY_KPI,   _WEEKLY_KPI_HEADERS)


def _group_for(name: str) -> str:
    from config import TEAM_MEMBERS
    return TEAM_MEMBERS.get(name, {}).get("group", "Unknown")


# ─────────────────────────────────────────
# Public Methods
# ─────────────────────────────────────────

def log_checkin(date: str, name: str, time_str: str, goals: list[str], status: str):
    try:
        ws = _get_or_create_tab(TAB_CHECKIN, _CHECKIN_HEADERS)
        status_icon = "✅ On-time" if status == "on-time" else ("⚠️ Late" if status == "late" else "❌ Missing")
        row = [
            date, name, _group_for(name), time_str,
            goals[0] if len(goals) > 0 else "",
            goals[1] if len(goals) > 1 else "",
            goals[2] if len(goals) > 2 else "",
            status_icon,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info("[Sheets] Check-in logged: %s %s", name, status)
    except Exception as e:
        log.error("[Sheets] log_checkin failed: %s", e)


def log_late_missing(date: str, name: str):
    try:
        ws = _get_or_create_tab(TAB_CHECKIN, _CHECKIN_HEADERS)
        row = [date, name, _group_for(name), "—", "—", "—", "—", "❌ Missing"]
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info("[Sheets] Marked missing: %s", name)
    except Exception as e:
        log.error("[Sheets] log_late_missing failed: %s", e)


def log_eod(date: str, name: str, time_str: str, links: list[str]):
    try:
        ws = _get_or_create_tab(TAB_EOD, _EOD_HEADERS)
        row = [
            date, name, _group_for(name), time_str,
            links[0] if len(links) > 0 else "",
            links[1] if len(links) > 1 else "",
            links[2] if len(links) > 2 else "",
            "✅ Submitted",
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info("[Sheets] EOD logged: %s (%d links)", name, len(links))
    except Exception as e:
        log.error("[Sheets] log_eod failed: %s", e)


def log_wiki_entry(
    timestamp: str, client: str, category: str,
    value: str, source_link: str, status: str,
):
    """Append one row to the Client_Wiki tab.

    Schema: Timestamp | Client | Category | Value | Source_Link | Status
    """
    try:
        ws = _get_or_create_tab(TAB_CLIENT_WIKI, _WIKI_HEADERS)
        row = [timestamp, client, category, value, source_link, status]
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info("[Sheets] Wiki entry logged: client=%s category=%s status=%s", client, category, status)
    except Exception as e:
        log.error("[Sheets] log_wiki_entry failed: %s", e)


def log_chat_archive(
    timestamp: str, space: str, user: str, message: str, link: str,
    summary: str = "",
):
    """Append one row to Chat_Archive — raw conversation memory bank for the Brain Wiki.

    Schema: Timestamp | Space | User | Message | Link | Summary
    Link: Google Chat message resource name or first extracted URL.
    Summary: 3-word AI label generated when the message contains a URL or file.
    """
    try:
        ws = _get_or_create_tab(TAB_CHAT_ARCHIVE, _ARCHIVE_HEADERS)
        row = [timestamp, space, user, message[:2000], link, summary]
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.debug("[Archive] Logged: space=%s user=%s summary=%r", space, user, summary)
    except Exception as e:
        log.error("[Archive] log_chat_archive failed: %s", e)


def log_sla_breach(
    date: str, time_str: str, space: str, tagger: str,
    tagged: str, thread_key: str, met: bool
):
    try:
        ws = _get_or_create_tab(TAB_SLA_LOG, _SLA_HEADERS)
        ping_count = _get_ping_count(tagged)
        row = [
            date, time_str, space, tagger, tagged,
            thread_key[:30],
            "✅ Yes" if met else "❌ No",
            ping_count,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        if not met:
            _increment_summary(tagged)
        log.info("[Sheets] SLA breach logged: %s tagged %s — met=%s", tagger, tagged, met)
    except Exception as e:
        log.error("[Sheets] log_sla_breach failed: %s", e)


def _get_ping_count(name: str) -> int:
    try:
        ws = _get_or_create_tab(TAB_SLA_LOG, _SLA_HEADERS)
        records = ws.get_all_records()
        return sum(1 for r in records if r.get("Tagged") == name and r.get("15-Min Met?") == "❌ No")
    except Exception:
        return 0


def _increment_summary(name: str):
    """Upsert the Ping Summary tab for a member."""
    try:
        ws = _get_or_create_tab(TAB_SUMMARY, _SUMMARY_HEADERS)
        records = ws.get_all_records()
        from config import TEAM_MEMBERS
        group = TEAM_MEMBERS.get(name, {}).get("group", "Unknown")
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")

        for i, rec in enumerate(records, start=2):  # Row 1 = header
            if rec.get("Name") == name:
                total = int(rec.get("Total Pings", 0)) + 1
                ws.update_cell(i, 5, total)    # Total Pings col
                ws.update_cell(i, 6, now_str)  # Last Breach col
                return

        # New row for this member
        ws.append_row(
            [name, group, 0, 0, 1, now_str],
            value_input_option="USER_ENTERED"
        )
    except Exception as e:
        log.error("[Sheets] _increment_summary failed: %s", e)


def log_weekly_kpi(
    week_label: str,
    date_from: str,
    date_to: str,
    checkins_ontime: int,
    checkins_late: int,
    checkins_missing: int,
    eods_submitted: int,
    eods_missing: int,
    sla_breaches: int,
    sla_met: int,
    tasks_created: int,
    tasks_completed: int,
    overwrite: bool = False,
) -> bool:
    """Append or update one weekly aggregate row in the Weekly_KPI tab.

    Returns True if a row was written/updated, False if skipped (duplicate).
    Skips silently when the same Week_Label already exists unless overwrite=True.
    """
    try:
        ws = _get_or_create_tab(TAB_WEEKLY_KPI, _WEEKLY_KPI_HEADERS)
        total_sla = sla_breaches + sla_met
        breach_rate = round(sla_breaches / total_sla * 100, 1) if total_sla else 0.0
        generated_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        row = [
            week_label, date_from, date_to,
            checkins_ontime, checkins_late, checkins_missing,
            eods_submitted, eods_missing,
            sla_breaches, sla_met, breach_rate,
            tasks_created, tasks_completed,
            generated_at,
        ]

        # Check for existing row with same Week_Label (col A)
        existing = ws.col_values(1)  # all values in column A (Week_Label)
        try:
            dup_row = existing.index(week_label) + 1  # 1-based row index
        except ValueError:
            dup_row = None

        if dup_row is not None:
            if not overwrite:
                log.info("[Sheets] Weekly KPI skipped (duplicate): week=%s row=%d", week_label, dup_row)
                return False
            # overwrite=True: update the existing row in place
            ws.update(f"A{dup_row}", [row], value_input_option="USER_ENTERED")
            log.info("[Sheets] Weekly KPI overwritten: week=%s row=%d", week_label, dup_row)
        else:
            ws.append_row(row, value_input_option="USER_ENTERED")
            log.info("[Sheets] Weekly KPI logged: week=%s checkins=%d/%d eod=%d/%d sla_breach=%d",
                     week_label, checkins_ontime, checkins_ontime + checkins_late + checkins_missing,
                     eods_submitted, eods_submitted + eods_missing, sla_breaches)
        return True
    except Exception as e:
        log.error("[Sheets] log_weekly_kpi failed: %s", e)
        return False
