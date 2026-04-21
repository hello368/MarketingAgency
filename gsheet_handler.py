"""
gsheet_handler.py — Google Sheets 연동 핸들러
scripts/gsheet_status.py의 공개 API를 re-export.
`from gsheet_handler import update_status` 형태로 사용.
"""
from scripts.gsheet_status import update_status, batch_update, get_all_statuses

__all__ = ["update_status", "batch_update", "get_all_statuses"]
