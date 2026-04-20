"""
Google Sheets 상태 업데이트 모듈
"Live Status" 탭의 A열(이름) → B열(상태) 실시간 업데이트

사용법:
    from scripts.gsheet_status import update_status, batch_update, get_all_statuses
    update_status("Michael", "Active - Botox Campaign")
"""

import os
import logging
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 설정 (환경 변수에서 로드)
# ─────────────────────────────────────────
SHEET_ID          = os.environ.get("GOOGLE_SHEET_ID", "1e_YQ9YBC_SCfM3Ex_rkg_f5NWlr5nOF9LBk3GT2TwZ8")
CREDENTIALS_FILE  = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
TAB_NAME          = os.environ.get("GOOGLE_SHEET_TAB", "Live Status")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheet() -> gspread.Worksheet:
    """인증 후 워크시트 반환"""
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(TAB_NAME)


def update_status(name: str, status: str) -> bool:
    """
    A열에서 name을 찾아 B열 상태를 업데이트.

    Args:
        name:   시트 A열의 이름 (예: "Michael")
        status: 업데이트할 상태 문자열 (예: "Active - Botox Campaign")

    Returns:
        True  = 성공
        False = 이름 없음 또는 오류
    """
    try:
        sheet = get_sheet()
        cell = sheet.find(name, in_column=1)

        if cell:
            sheet.update_cell(cell.row, 2, status)
            log.info(f"[GSheet] {name} → '{status}'")
            print(f"✅ 성공: {name}의 상태를 '{status}'(으)로 업데이트했습니다.")
            return True
        else:
            log.warning(f"[GSheet] '{name}' 이름을 시트에서 찾을 수 없습니다.")
            print(f"❌ 실패: 시트에서 '{name}' 이름을 찾을 수 없습니다.")
            return False

    except Exception as e:
        log.error(f"[GSheet ERROR] update_status({name}): {e}")
        print(f"❌ 에러 발생: {e}")
        return False


def batch_update(updates: dict[str, str]) -> dict[str, bool]:
    """
    여러 이름을 한 번에 업데이트 (API 호출 최소화).

    Args:
        updates: {"Michael": "Active", "Jane": "On Leave", ...}

    Returns:
        {"Michael": True, "Jane": False, ...}
    """
    results = {}
    try:
        sheet = get_sheet()
        all_names = sheet.col_values(1)  # A열 전체 읽기 (1회 API 호출)

        cell_updates = []
        for name, status in updates.items():
            try:
                row = all_names.index(name) + 1  # 1-based
                cell_updates.append({
                    "range": f"B{row}",
                    "values": [[status]],
                })
                results[name] = True
                log.info(f"[GSheet batch] {name} → '{status}'")
            except ValueError:
                log.warning(f"[GSheet batch] '{name}' 없음")
                results[name] = False

        if cell_updates:
            sheet.batch_update(cell_updates)
            print(f"✅ 일괄 업데이트 완료: {sum(results.values())}/{len(updates)}건")

    except Exception as e:
        log.error(f"[GSheet ERROR] batch_update: {e}")
        print(f"❌ 에러 발생: {e}")
        for name in updates:
            results.setdefault(name, False)

    return results


def get_all_statuses() -> dict[str, str]:
    """
    시트 전체의 이름:상태 딕셔너리 반환.

    Returns:
        {"Michael": "Active", "Jane": "On Leave", ...}
    """
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        # 헤더 행 건너뛰기 (첫 행이 헤더인 경우)
        data_rows = rows[1:] if rows and rows[0][0].lower() in ("name", "이름") else rows
        return {
            row[0]: row[1] if len(row) > 1 else ""
            for row in data_rows
            if row and row[0]
        }
    except Exception as e:
        log.error(f"[GSheet ERROR] get_all_statuses: {e}")
        return {}


# ─────────────────────────────────────────
# CLI 직접 실행용
# ─────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3:
        # python scripts/gsheet_status.py "Michael" "Active"
        _, _name, _status = sys.argv
        update_status(_name, _status)
    elif len(sys.argv) == 1:
        # 전체 상태 출력
        statuses = get_all_statuses()
        if statuses:
            print("\n📋 현재 Live Status:")
            for n, s in statuses.items():
                print(f"  {n:20s} → {s}")
        else:
            print("데이터 없음 또는 연결 실패")
    else:
        print("사용법: python scripts/gsheet_status.py [이름] [상태]")
