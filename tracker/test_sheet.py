"""
MARTS — Google Sheet Connectivity Debug Script
Run this standalone to diagnose exactly why the sheet update is failing.

Usage:
    cd tracker/
    python test_sheet.py
"""
import os
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

SHEET_ID    = "1e_YQ9YBC_SCfM3Ex_rkg_f5NWlr5nOF9LBk3GT2TwZ8"
CREDS_PATHS = [
    "./credentials/service_account.json",
    "./credentials.json",
    "./service_account.json",
    "../credentials.json",
]
TZ = ZoneInfo("Asia/Manila")

SEP = "─" * 60


def step(n, label):
    print(f"\n{SEP}\nSTEP {n}: {label}\n{SEP}")


# ──────────────────────────────────────────────────────────────
# STEP 1 — Locate credentials file
# ──────────────────────────────────────────────────────────────
step(1, "Locate credentials.json")

creds_path = None
for path in CREDS_PATHS:
    exists = os.path.exists(path)
    print(f"  {'✅' if exists else '❌'}  {os.path.abspath(path)}")
    if exists and creds_path is None:
        creds_path = path

if creds_path is None:
    print("\n❌ FATAL: No credentials file found in any expected location.")
    print("   ACTION: Place your Google Service Account JSON at:")
    print(f"           {os.path.abspath('./credentials/service_account.json')}")
    sys.exit(1)

print(f"\n✅ Using: {os.path.abspath(creds_path)}")

# ──────────────────────────────────────────────────────────────
# STEP 2 — Import gspread / google-auth
# ──────────────────────────────────────────────────────────────
step(2, "Import libraries (gspread, google-auth)")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    print(f"  ✅ gspread version : {gspread.__version__}")
    import google.auth
    print(f"  ✅ google-auth OK")
except ImportError as e:
    print(f"\n❌ FATAL: Missing library — {e}")
    print("   ACTION: Run:  pip install gspread google-auth")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 3 — Load service account credentials
# ──────────────────────────────────────────────────────────────
step(3, "Load service account credentials from JSON")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

try:
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    print(f"  ✅ Service account email : {creds.service_account_email}")
    print(f"  ✅ Project ID            : {creds.project_id}")
except Exception as e:
    print(f"\n❌ FATAL: Could not parse credentials file.")
    print(f"   Error: {e}")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 4 — Authorize gspread client
# ──────────────────────────────────────────────────────────────
step(4, "Authorize gspread client")

try:
    client = gspread.authorize(creds)
    print("  ✅ gspread client authorized")
except Exception as e:
    print(f"\n❌ FATAL: gspread authorization failed.")
    print(f"   Error: {e}")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 5 — Open spreadsheet by ID
# ──────────────────────────────────────────────────────────────
step(5, f"Open spreadsheet  ID={SHEET_ID}")

try:
    spreadsheet = client.open_by_key(SHEET_ID)
    print(f"  ✅ Spreadsheet title : '{spreadsheet.title}'")
    print(f"  ✅ Spreadsheet URL   : {spreadsheet.url}")
    sheets = [ws.title for ws in spreadsheet.worksheets()]
    print(f"  ✅ Existing tabs     : {sheets}")
except gspread.exceptions.APIError as e:
    print(f"\n❌ API ERROR opening spreadsheet:")
    print(f"   Status  : {e.response.status_code}")
    print(f"   Message : {e.response.json()}")
    print("\n   LIKELY CAUSES:")
    print("   • The sheet is NOT shared with the service account email shown in Step 3")
    print(f"     → Go to Google Sheets → Share → add {creds.service_account_email} as Editor")
    print("   • The Sheet ID is wrong")
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"\n❌ FATAL: {e}")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 6 — Get or create "Live Status" tab
# ──────────────────────────────────────────────────────────────
step(6, "Get or create 'Live Status' worksheet")

TAB_NAME = "Live Status"

try:
    try:
        ws = spreadsheet.worksheet(TAB_NAME)
        print(f"  ✅ Tab '{TAB_NAME}' already exists")
        all_values = ws.get_all_values()
        print(f"  ✅ Rows in tab (including header): {len(all_values)}")
        if all_values:
            print(f"  ✅ Header row: {all_values[0]}")
    except gspread.exceptions.WorksheetNotFound:
        print(f"  ⚠️  Tab '{TAB_NAME}' not found — creating it now...")
        ws = spreadsheet.add_worksheet(title=TAB_NAME, rows=20, cols=4)
        headers = ["Name", "Group", "Status", "Last Updated"]
        ws.append_row(headers, value_input_option="USER_ENTERED")
        members = [
            ("Michael", "Top Leader"),
            ("Kaye",    "Management & Ops"),
            ("Anna",    "Management & Ops"),
            ("Ivan",    "Tech & Dev"),
            ("Izzy",    "Tech & Dev"),
            ("Kevin",   "Tech & Dev"),
            ("Milo",    "Tech & Dev"),
            ("Tiffany", "Ads & Growth"),
            ("Danni",   "Ads & Growth"),
            ("Silver",  "Creative"),
            ("Jhon",    "Creative"),
            ("Lovely",  "Sales Support"),
        ]
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        rows = [[name, group, "⚪ Offline", now_str] for name, group in members]
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  ✅ Tab created with {len(members)} members")
except Exception as e:
    print(f"\n❌ FATAL: Could not access/create '{TAB_NAME}' tab.")
    print(f"   Error: {e}")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 7 — Find "Ivan" row
# ──────────────────────────────────────────────────────────────
step(7, "Find 'Ivan' in column A")

try:
    all_values = ws.get_all_values()
    ivan_row = None
    for i, row in enumerate(all_values, start=1):
        if row and row[0].strip().lower() == "ivan":
            ivan_row = i
            print(f"  ✅ Found 'Ivan' at row {ivan_row}  →  {row}")
            break

    if ivan_row is None:
        print("  ❌ 'Ivan' NOT found in column A.")
        print(f"     All column A values: {[r[0] for r in all_values if r]}")
        sys.exit(1)
except Exception as e:
    print(f"\n❌ FATAL: Could not read worksheet data.")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# STEP 8 — Write status to Ivan's row
# ──────────────────────────────────────────────────────────────
step(8, "Write '🟢 Online (System Initialized)' to Ivan's Status cell")

try:
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    # Sheet columns: A=Name, B=Current Status, C=Last Active
    ws.update(
        [["🟢 Online (System Initialized)", now_str]],
        f"B{ivan_row}:C{ivan_row}",
        value_input_option="USER_ENTERED",
    )
    print(f"  ✅ SUCCESS — B{ivan_row} (Current Status) = '🟢 Online (System Initialized)'")
    print(f"  ✅ C{ivan_row} (Last Active) = '{now_str}'")
except gspread.exceptions.APIError as e:
    print(f"\n❌ API ERROR writing to cell:")
    print(f"   Status  : {e.response.status_code}")
    print(f"   Message : {e.response.json()}")
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"\n❌ FATAL: Write failed.")
    traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# RESULT
# ──────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("ALL STEPS PASSED ✅")
print(f"Open your Google Sheet and check the 'Live Status' tab.")
print(f"Ivan's row should now show: 🟢 Online (System Initialized)")
print(SEP)
