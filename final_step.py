import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account

load_dotenv()
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build('sheets', 'v4', credentials=creds)

def add_tab(name):
    try:
        body = {'requests': [{'addSheet': {'properties': {'title': name}}}]}
        service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        print(f"✅ {name} 탭 생성 성공!")
    except Exception as e:
        print(f"❌ {name} 생성 실패 (혹시 이미 있나요?): {e}")

add_tab("Chat_Archive")
add_tab("Client_Wiki")
