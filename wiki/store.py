"""
Wiki Store — Client data query interface for the Client_Wiki and Chat_Archive tabs.

Reads all Verified records for a given client name and generates a professional
executive summary using WikiModelRouter.analyst_chat() (claude-3.5-sonnet,
cost-isolated from task_tracker). Also searches Chat_Archive for raw
conversation context about any keyword or client.

Usage
-----
    from wiki.store import WikiStore
    from wiki.interceptor import WikiModelRouter

    store = WikiStore()
    store.set_router(WikiModelRouter())

    result = store.query_client("Luna Medspa")
    print(result.format_reply())

    hits = store.search_archive("Luna")
    for row in hits:
        print(row["Timestamp"], row["User"], row["Message"])
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

TAB_CLIENT_WIKI  = "Client_Wiki"
TAB_CHAT_ARCHIVE = "Chat_Archive"

_WIKI_HEADERS    = ["Timestamp", "Client", "Category", "Value", "Source_Link", "Status"]
_ARCHIVE_HEADERS = ["Timestamp", "Space", "User", "Message", "Link", "Summary"]

_SUMMARY_SYSTEM = """\
You are a professional reporting assistant for a marketing agency.
You receive structured client data (verified records) plus raw conversation
excerpts from the agency's Chat Archive.
Write a 1-3 sentence executive summary in professional business English.

Rules:
- Lead with the client name
- Prioritize: monthly plan / service fee, ad budget, account manager / assignee, active project
- If relevant conversation excerpts are provided, weave in key context naturally
- Use prose only — no bullet points, no headers
- Do not speculate or add information beyond what is provided
- Example: "GlowSpa is currently on a $1,509/mo retainer with a $100/day ad budget, \
managed by Tiffany. The Q2 campaign targets local lead generation."
"""


@dataclass
class WikiQueryResult:
    client_name: str
    rows: list = field(default_factory=list)
    archive_rows: list = field(default_factory=list)
    summary: str = ""
    verified_count: int = 0
    pending_count: int = 0
    not_found: bool = False

    def format_reply(self) -> str:
        if self.not_found or (not self.rows and not self.pending_count and not self.archive_rows):
            return (
                f"No data found for *{self.client_name}* in the Client Wiki or Chat Archive.\n"
                "_This client may not have been discussed yet._"
            )
        if not self.rows and not self.archive_rows:
            return (
                f"*{self.client_name}* has {self.pending_count} pending "
                "record(s) awaiting verification. No verified data is available yet."
            )

        lines = [
            f"*Client Wiki — {self.client_name}*",
            "",
            self.summary,
            "",
        ]
        suffix = f"_{self.verified_count} verified record(s)"
        if self.pending_count:
            suffix += f" · {self.pending_count} pending"
        if self.archive_rows:
            suffix += f" · {len(self.archive_rows)} chat message(s)"
        suffix += "_"
        lines.append(suffix)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "client_name":    self.client_name,
            "summary":        self.summary,
            "verified_count": self.verified_count,
            "pending_count":  self.pending_count,
            "not_found":      self.not_found,
            "records":        self.rows,
            "chat_archive":   self.archive_rows,
        }


class WikiStore:
    """
    Reads Client_Wiki and Chat_Archive Google Sheet tabs.
    Thread-safe; lazily connects to Sheets on first query.

    Parameters
    ----------
    creds_path : str | None
        Path to the service account JSON. Falls back to GOOGLE_CREDENTIALS_PATH env var.
    spreadsheet_id : str | None
        Google Sheets ID. Falls back to SPREADSHEET_ID env var.
    """

    def __init__(
        self,
        creds_path: str | None = None,
        spreadsheet_id: str | None = None,
    ):
        self._creds_path     = creds_path or os.environ.get(
            "GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json"
        )
        self._spreadsheet_id = spreadsheet_id or os.environ.get("SPREADSHEET_ID", "")
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._lock   = threading.Lock()
        self._router = None  # injected via set_router()

    def set_router(self, router) -> None:
        """Inject a WikiModelRouter instance for LLM-generated summaries."""
        self._router = router

    # ── Public API ────────────────────────────────────────────────────────────

    def query_client(self, client_name: str) -> WikiQueryResult:
        """
        Fetch verified records from Client_Wiki AND matching messages from
        Chat_Archive, then generate a combined executive summary.

        Client matching is bidirectional substring (case-insensitive).
        """
        # ── Client_Wiki records ──────────────────────────────────────────────
        try:
            ws      = self._get_tab(TAB_CLIENT_WIKI, _WIKI_HEADERS)
            records = ws.get_all_records()
        except Exception as exc:
            log.error("[WikiStore] Client_Wiki read failed: %s", exc)
            records = []

        matched  = self._filter_client(records, client_name)
        verified = [r for r in matched if str(r.get("Status", "")).strip().lower() == "verified"]
        pending  = [r for r in matched if str(r.get("Status", "")).strip().lower() == "pending"]

        # ── Chat_Archive messages ────────────────────────────────────────────
        archive_rows = self._fetch_archive_context(client_name)

        if not matched and not archive_rows:
            log.info("[WikiStore] No data found for %r in either tab", client_name)
            return WikiQueryResult(client_name=client_name, not_found=True)

        summary = self._build_summary(client_name, verified, archive_rows)

        log.info(
            "[WikiStore] query=%r verified=%d pending=%d archive=%d",
            client_name, len(verified), len(pending), len(archive_rows),
        )
        return WikiQueryResult(
            client_name=client_name,
            rows=verified,
            archive_rows=archive_rows,
            summary=summary,
            verified_count=len(verified),
            pending_count=len(pending),
            not_found=False,
        )

    def search_archive(self, keyword: str, limit: int = 50) -> list[dict]:
        """
        Full-text search of Chat_Archive for any keyword.
        Returns up to `limit` most-recent matching rows.
        """
        try:
            ws      = self._get_tab(TAB_CHAT_ARCHIVE, _ARCHIVE_HEADERS)
            records = ws.get_all_records()
        except Exception as exc:
            log.error("[WikiStore] Chat_Archive search failed: %s", exc)
            return []

        q = keyword.strip().lower()
        hits = [
            r for r in records
            if q in str(r.get("Message", "")).lower()
            or q in str(r.get("Space", "")).lower()
            or q in str(r.get("User", "")).lower()
        ]
        return hits[-limit:]

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_tab(self, title: str, headers: list[str]) -> gspread.Worksheet:
        with self._lock:
            if self._spreadsheet is None:
                creds  = Credentials.from_service_account_file(
                    self._creds_path, scopes=_SCOPES
                )
                client = gspread.authorize(creds)
                self._spreadsheet = client.open_by_key(self._spreadsheet_id)
        try:
            return self._spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(
                title=title, rows=1000, cols=len(headers)
            )
            ws.append_row(headers, value_input_option="USER_ENTERED")
            ws.format("1:1", {"textFormat": {"bold": True}})
            log.info("[WikiStore] Created tab: %s", title)
            return ws

    def _filter_client(self, records: list, client_name: str) -> list:
        q = client_name.strip().lower()
        return [
            r for r in records
            if q in str(r.get("Client", "")).strip().lower()
            or str(r.get("Client", "")).strip().lower() in q
        ]

    def _fetch_archive_context(self, keyword: str, limit: int = 20) -> list[dict]:
        """Return up to `limit` Chat_Archive rows mentioning the keyword."""
        try:
            ws      = self._get_tab(TAB_CHAT_ARCHIVE, _ARCHIVE_HEADERS)
            records = ws.get_all_records()
        except Exception as exc:
            log.warning("[WikiStore] Archive context fetch failed: %s", exc)
            return []

        q = keyword.strip().lower()
        hits = [r for r in records if q in str(r.get("Message", "")).lower()]
        return hits[-limit:]

    def _build_summary(
        self, client_name: str, rows: list, archive_rows: list
    ) -> str:
        """Generate summary via LLM incorporating both structured and chat data."""
        if not rows and not archive_rows:
            return "No data available for this client."

        data_lines = [
            f"- {r.get('Category', '?')}: {r.get('Value', '?')}"
            + (f" [source: {r['Source_Link']}]" if r.get("Source_Link") else "")
            for r in rows
        ] or ["(no verified structured records)"]

        archive_lines = [
            f"- [{r.get('Timestamp', '?')}] {r.get('User', '?')}: {str(r.get('Message', ''))[:200]}"
            for r in archive_rows[-10:]
        ]

        prompt_parts = [f"Client: {client_name}\n"]
        prompt_parts.append("Verified data:\n" + "\n".join(data_lines))
        if archive_lines:
            prompt_parts.append("\nRecent chat excerpts mentioning this client:\n" + "\n".join(archive_lines))
        prompt_parts.append("\nWrite a professional 1-3 sentence executive summary.")
        prompt = "\n".join(prompt_parts)

        if self._router is not None:
            try:
                result = self._router.analyst_chat(
                    prompt, system=_SUMMARY_SYSTEM, temperature=0.2,
                )
                return result.content.strip()
            except Exception as exc:
                log.warning("[WikiStore] LLM summary failed — using fallback: %s", exc)

        return self._rule_based_summary(client_name, rows)

    def _rule_based_summary(self, client_name: str, rows: list) -> str:
        data = {
            r.get("Category", "").strip().lower(): r.get("Value", "").strip()
            for r in rows
        }
        fee    = (
            data.get("service fee") or data.get("monthly fee")
            or data.get("retainer") or data.get("plan")
        )
        budget = (
            data.get("ad budget") or data.get("budget")
            or data.get("daily budget") or data.get("advertising budget")
        )
        mgr    = (
            data.get("assignee") or data.get("account manager")
            or data.get("managed by")
        )
        proj   = (
            data.get("project name") or data.get("project")
            or data.get("campaign")
        )

        parts: list[str] = [client_name]
        if fee:
            parts.append(f"is on a {fee} plan")
        if budget:
            parts.append(f"with a {budget} ad budget")
        if mgr:
            parts.append(f"managed by {mgr}")

        base = ", ".join(parts[:3]) + "."
        if proj:
            base += f" Active project: {proj}."
        return base
