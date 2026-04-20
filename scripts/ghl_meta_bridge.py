"""
GHL × Meta API Bridge
MedSpa Lead-Gen Agency — 자동화 통합 서버

Flow 1: Meta Lead Webhook → GHL Contact 생성
Flow 2: GHL Booking Webhook → Meta CAPI 전환 이벤트
Flow 3: 일일 스케줄러 → 퍼포먼스 대시보드 (Telegram)
"""

import os
import hashlib
import hmac
import json
import time
import uuid
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import httpx
import telebot
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 환경 변수
# ─────────────────────────────────────────
META_ACCESS_TOKEN  = os.environ.get("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")  # "act_XXXXXXXXXX"
META_PIXEL_ID      = os.environ.get("META_PIXEL_ID", "")
META_VERIFY_TOKEN  = os.environ.get("META_VERIFY_TOKEN", "dev-token")

GHL_API_KEY        = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID    = os.environ.get("GHL_LOCATION_ID", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# 미설정 키 경고 (서버는 기동, API 호출 시 실패)
_missing = [k for k, v in {
    "META_ACCESS_TOKEN": META_ACCESS_TOKEN,
    "META_AD_ACCOUNT_ID": META_AD_ACCOUNT_ID,
    "META_PIXEL_ID": META_PIXEL_ID,
    "GHL_API_KEY": GHL_API_KEY,
    "GHL_LOCATION_ID": GHL_LOCATION_ID,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
}.items() if not v]
if _missing:
    logging.warning(f"⚠️  .env 미설정 키: {', '.join(_missing)} — .env 파일을 채워주세요")

DB_PATH = os.environ.get("DB_PATH", "./data/medspa_bridge.db")

# BUG-03 FIX: 시술별 예약 가치 (env에서 로드, 기본값 fallback)
TREATMENT_VALUES: dict = json.loads(os.environ.get(
    "TREATMENT_VALUES",
    '{"botox": 350, "dysport": 350, "filler": 800, "hifu": 2000, '
    '"morpheus8": 1500, "prp": 600, "laser": 900, "default": 300}'
))

GHL_BASE = "https://services.leadconnectorhq.com"
META_GRAPH = "https://graph.facebook.com/v19.0"

# ─────────────────────────────────────────
# 앱 & DB 초기화
# ─────────────────────────────────────────
app = FastAPI(title="MedSpa GHL-Meta Bridge")

# Telegram bot — 토큰 미설정 시 None (API 호출은 건너뜀)
try:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
except Exception:
    bot = None
    log.warning("Telegram bot 초기화 실패 — TELEGRAM_BOT_TOKEN을 .env에 설정하세요")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meta_lead_id TEXT UNIQUE,
            ghl_contact_id TEXT,
            meta_ad_id TEXT,
            meta_adset_id TEXT,
            meta_campaign_id TEXT,
            meta_form_id TEXT,
            email TEXT,
            phone TEXT,
            first_name TEXT,
            last_name TEXT,
            treatment TEXT,
            meta_fbclid TEXT,
            meta_fbc TEXT,
            meta_fbp TEXT,
            created_at TEXT
        )
    """)
    # BUG-02 FIX: fbclid 컬럼이 없는 기존 DB에 마이그레이션
    for col in ("meta_fbclid", "meta_fbc", "meta_fbp"):
        try:
            con.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
        except Exception:
            pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ghl_appointment_id TEXT UNIQUE,
            ghl_contact_id TEXT,
            meta_lead_id TEXT,
            capi_sent INTEGER DEFAULT 0,
            booked_at TEXT,
            treatment TEXT
        )
    """)
    con.commit()
    con.close()


init_db()


# ─────────────────────────────────────────
# 헬퍼: 해싱 (Meta CAPI 요구사항)
# ─────────────────────────────────────────
def sha256(value: str) -> str:
    """Meta CAPI용 SHA-256 해싱 (소문자 트리밍 후)"""
    if not value:
        return ""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def normalize_phone(phone: str) -> str:
    """E.164 형식으로 정규화 (숫자만 추출, 미국 기본)"""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


# ─────────────────────────────────────────
# GHL API 클라이언트
# ─────────────────────────────────────────
class GHLClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }

    async def create_contact(self, data: dict) -> dict:
        payload = {
            "locationId": GHL_LOCATION_ID,
            "firstName": data.get("first_name", ""),
            "lastName": data.get("last_name", ""),
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "customFields": [
                {"key": "meta_ad_id",       "field_value": data.get("meta_ad_id", "")},
                {"key": "meta_adset_id",    "field_value": data.get("meta_adset_id", "")},
                {"key": "meta_campaign_id", "field_value": data.get("meta_campaign_id", "")},
                {"key": "meta_form_id",     "field_value": data.get("meta_form_id", "")},
                {"key": "meta_lead_id",     "field_value": data.get("meta_lead_id", "")},
                {"key": "treatment",        "field_value": data.get("treatment", "")},
            ],
            "source": "Meta Lead Ad",
            "tags": ["meta-lead", data.get("treatment", "").lower().replace(" ", "-")],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{GHL_BASE}/contacts/", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def get_contacts_today(self) -> list:
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{GHL_BASE}/contacts/",
                headers=self.headers,
                params={"locationId": GHL_LOCATION_ID, "startAfter": yesterday, "limit": 100},
            )
            r.raise_for_status()
            return r.json().get("contacts", [])

    async def get_appointments_today(self) -> list:
        yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        today = datetime.utcnow().isoformat() + "Z"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{GHL_BASE}/appointments/",
                headers=self.headers,
                params={"locationId": GHL_LOCATION_ID, "startTime": yesterday, "endTime": today},
            )
            r.raise_for_status()
            return r.json().get("appointments", [])


# ─────────────────────────────────────────
# Meta API 클라이언트
# ─────────────────────────────────────────
class MetaClient:
    def __init__(self):
        self.token = META_ACCESS_TOKEN

    async def get_lead_data(self, lead_id: str) -> dict:
        """Meta Lead ID로 폼 필드 상세 데이터 조회"""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{META_GRAPH}/{lead_id}",
                params={
                    "fields": "id,created_time,field_data,ad_id,adset_id,campaign_id,form_id",
                    "access_token": self.token,
                },
            )
            r.raise_for_status()
            return r.json()

    async def send_capi_event(self, event_name: str, contact: dict, custom_data: dict = None) -> dict:
        """Meta Conversions API로 전환 이벤트 전송"""
        # BUG-M01 FIX: event_id 생성 — Meta 이벤트 중복 집계 방지
        # email prefix + timestamp 조합으로 동일 이벤트 재전송 시 dedup 가능
        email_prefix = sha256(contact.get("email", ""))[:12]
        event_id = f"evt_{event_name}_{int(time.time())}_{email_prefix}"

        user_data = {
            "em": [sha256(contact.get("email", ""))],
            "ph": [sha256(normalize_phone(contact.get("phone", "")))],
            "fn": [sha256(contact.get("first_name", ""))],
            "ln": [sha256(contact.get("last_name", ""))],
        }
        # BUG-M02 FIX: fbc / fbp 포함 — EMQ +2~3점 회복
        if contact.get("fbc"):
            user_data["fbc"] = contact["fbc"]
        if contact.get("fbp"):
            user_data["fbp"] = contact["fbp"]

        # 빈 해시 제거 (fbc/fbp는 문자열이므로 별도 처리)
        empty_hash = sha256("")
        user_data = {
            k: v for k, v in user_data.items()
            if v != [empty_hash] and v != "" and v is not None
        }

        payload = {
            "data": [{
                "event_id": event_id,           # BUG-M01 FIX
                "event_name": event_name,
                "event_time": int(time.time()),
                "action_source": "crm",
                "user_data": user_data,
                "custom_data": custom_data or {},
            }],
            "access_token": self.token,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{META_GRAPH}/{META_PIXEL_ID}/events", json=payload)
            r.raise_for_status()
            log.info(f"[CAPI] event_id={event_id} event={event_name} sent")
            return r.json()

    async def get_insights(self, date_preset: str = "yesterday") -> list:
        """캠페인별 광고 인사이트 조회"""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{META_GRAPH}/{META_AD_ACCOUNT_ID}/insights",
                params={
                    "fields": "campaign_id,campaign_name,adset_id,adset_name,"
                              "spend,impressions,clicks,actions,cost_per_action_type",
                    "level": "adset",
                    "date_preset": date_preset,
                    "access_token": self.token,
                },
            )
            r.raise_for_status()
            return r.json().get("data", [])


ghl = GHLClient()
meta = MetaClient()


# ─────────────────────────────────────────
# Flow 1: Meta Lead Webhook → GHL Contact
# ─────────────────────────────────────────
@app.get("/webhook/meta-lead")
async def meta_webhook_verify(request: Request):
    """Meta Webhook 검증 (최초 등록 시)"""
    params = dict(request.query_params)
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == META_VERIFY_TOKEN):
        return PlainTextResponse(params["hub.challenge"])
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/meta-lead")
async def meta_lead_webhook(request: Request, bg: BackgroundTasks):
    """Meta에서 새 리드 발생 시 실시간 수신"""
    body = await request.json()
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            lead_id = change["value"].get("leadgen_id")
            if lead_id:
                bg.add_task(process_meta_lead, lead_id)
    return {"status": "ok"}


async def process_meta_lead(lead_id: str):
    """Meta Lead ID → 상세 데이터 조회 → GHL Contact 생성"""
    try:
        lead_data = await meta.get_lead_data(lead_id)
        fields = {f["name"]: f["values"][0] for f in lead_data.get("field_data", []) if f.get("values")}

        full_name = fields.get("full_name", "")
        name_parts = full_name.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # BUG-M02 FIX: fbclid 캡처
        # 랜딩 페이지 hidden field 또는 Meta 리드폼 커스텀 필드로 전달된 fbclid 추출
        fbclid = (fields.get("fbclid") or fields.get("fb_clid") or
                  fields.get("fbclid_param") or "")
        # fbc 표준 포맷: fb.1.<unix_ms>.<fbclid>
        fbc = f"fb.1.{int(time.time() * 1000)}.{fbclid}" if fbclid else ""
        # fbp는 브라우저 픽셀 쿠키 — hidden field로 전달된 경우 캡처
        fbp = fields.get("fbp", "") or fields.get("_fbp", "")

        contact_data = {
            "meta_lead_id": lead_id,
            "first_name": fields.get("first_name", first_name),
            "last_name": fields.get("last_name", last_name),
            "email": fields.get("email", ""),
            "phone": normalize_phone(fields.get("phone_number", "")),
            "treatment": fields.get("treatment_interest", ""),
            "meta_ad_id": lead_data.get("ad_id", ""),
            "meta_adset_id": lead_data.get("adset_id", ""),
            "meta_campaign_id": lead_data.get("campaign_id", ""),
            "meta_form_id": lead_data.get("form_id", ""),
            "fbclid": fbclid,
            "fbc": fbc,
            "fbp": fbp,
        }

        result = await ghl.create_contact(contact_data)
        ghl_contact_id = result.get("contact", {}).get("id", "")

        # DB에 저장 (리드-예약 연결용, fbclid/fbc/fbp 포함)
        con = sqlite3.connect(DB_PATH)
        con.execute(
            """INSERT OR IGNORE INTO leads
               (meta_lead_id, ghl_contact_id, meta_ad_id, meta_adset_id,
                meta_campaign_id, meta_form_id, email, phone, first_name,
                last_name, treatment, meta_fbclid, meta_fbc, meta_fbp, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lead_id, ghl_contact_id, contact_data["meta_ad_id"],
             contact_data["meta_adset_id"], contact_data["meta_campaign_id"],
             contact_data["meta_form_id"], contact_data["email"],
             contact_data["phone"], contact_data["first_name"],
             contact_data["last_name"], contact_data["treatment"],
             fbclid, fbc, fbp,
             datetime.utcnow().isoformat()),
        )
        if fbclid:
            log.info(f"[Lead Sync] fbclid captured → fbc={fbc[:30]}…")
        con.commit()
        con.close()

        log.info(f"[Lead Sync] meta_lead_id={lead_id} → ghl_contact_id={ghl_contact_id}")

    except Exception as e:
        log.error(f"[Lead Sync ERROR] lead_id={lead_id}: {e}")


# ─────────────────────────────────────────
# Flow 2: GHL Booking Webhook → Meta CAPI
# ─────────────────────────────────────────
@app.post("/webhook/ghl-booking")
async def ghl_booking_webhook(request: Request, bg: BackgroundTasks):
    """GHL Appointment 생성 시 Meta CAPI로 'Schedule' 이벤트 전송"""
    body = await request.json()
    appointment_id = body.get("id") or body.get("appointmentId")
    contact_id = body.get("contactId")
    treatment = body.get("calendarTitle", "")
    booked_at = body.get("startTime", datetime.utcnow().isoformat())

    if appointment_id and contact_id:
        bg.add_task(process_ghl_booking, appointment_id, contact_id, treatment, booked_at)

    return {"status": "ok"}


async def process_ghl_booking(appointment_id: str, contact_id: str, treatment: str, booked_at: str):
    """GHL 예약 → DB에서 Meta 정보 조회 → CAPI 전송"""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT * FROM leads WHERE ghl_contact_id=?", (contact_id,)
        ).fetchone()

        if row:
            columns = [d[0] for d in con.execute("PRAGMA table_info(leads)").fetchall()]
            lead = dict(zip(columns, row))

            contact = {
                "email":      lead["email"],
                "phone":      lead["phone"],
                "first_name": lead["first_name"],
                "last_name":  lead["last_name"],
                "fbc":        lead.get("meta_fbc", ""),   # BUG-M02 FIX
                "fbp":        lead.get("meta_fbp", ""),   # BUG-M02 FIX
            }
            custom_data = {
                "value": 150.00,       # 평균 예약 가치 (클라이언트별 조정)
                "currency": "USD",
                "treatment_type": treatment or lead.get("treatment", ""),
            }

            result = await meta.send_capi_event("Schedule", contact, custom_data)
            capi_sent = 1 if result.get("events_received", 0) > 0 else 0

            con.execute(
                """INSERT OR IGNORE INTO bookings
                   (ghl_appointment_id, ghl_contact_id, meta_lead_id, capi_sent, booked_at, treatment)
                   VALUES (?,?,?,?,?,?)""",
                (appointment_id, contact_id, lead["meta_lead_id"],
                 capi_sent, booked_at, treatment),
            )
            con.commit()
            log.info(f"[CAPI] appointment={appointment_id} → Schedule event sent (capi_sent={capi_sent})")
        else:
            # Meta에서 유입되지 않은 예약 (직접 유입 등) — 기록만
            con.execute(
                """INSERT OR IGNORE INTO bookings
                   (ghl_appointment_id, ghl_contact_id, capi_sent, booked_at, treatment)
                   VALUES (?,?,0,?,?)""",
                (appointment_id, contact_id, booked_at, treatment),
            )
            con.commit()
            log.info(f"[CAPI] appointment={appointment_id}: no matching meta lead, skipped CAPI")

        con.close()

    except Exception as e:
        log.error(f"[CAPI ERROR] appointment={appointment_id}: {e}")


# ─────────────────────────────────────────
# Flow 3: 일일 대시보드 자동화
# ─────────────────────────────────────────
def extract_action_count(actions: list, action_type: str) -> int:
    for a in (actions or []):
        if a.get("action_type") == action_type:
            return int(float(a.get("value", 0)))
    return 0


async def build_daily_report():
    """Meta Insights + GHL 데이터를 합산해 일일 리포트 생성"""
    try:
        insights = await meta.get_insights("yesterday")
        ghl_contacts = await ghl.get_contacts_today()
        ghl_appointments = await ghl.get_appointments_today()

        # Meta 집계
        total_spend = sum(float(i.get("spend", 0)) for i in insights)
        total_impressions = sum(int(i.get("impressions", 0)) for i in insights)
        total_clicks = sum(int(i.get("clicks", 0)) for i in insights)
        total_meta_leads = sum(extract_action_count(i.get("actions", []), "lead") for i in insights)

        # GHL 집계
        total_ghl_leads = len(ghl_contacts)
        total_bookings = len(ghl_appointments)

        # KPI 계산
        cpl = total_spend / total_meta_leads if total_meta_leads else 0
        cpb = total_spend / total_bookings if total_bookings else 0
        lead_to_booking_cvr = (total_bookings / total_ghl_leads * 100) if total_ghl_leads else 0

        # 캠페인별 상세
        campaign_lines = []
        for i in insights:
            spend = float(i.get("spend", 0))
            leads = extract_action_count(i.get("actions", []), "lead")
            name = i.get("campaign_name", "Unknown Campaign")[:30]
            cpl_i = spend / leads if leads else 0
            campaign_lines.append(f"  • {name}: ${spend:.0f} / {leads} leads (CPL ${cpl_i:.0f})")

        campaign_section = "\n".join(campaign_lines) if campaign_lines else "  • 데이터 없음"

        report = (
            f"📊 *MedSpa Daily Report — {datetime.utcnow().strftime('%Y-%m-%d')}*\n\n"
            f"💰 *Meta 광고 성과*\n"
            f"  • 총 지출: ${total_spend:.2f}\n"
            f"  • 노출: {total_impressions:,}\n"
            f"  • 클릭: {total_clicks:,}\n"
            f"  • 리드 (Meta): {total_meta_leads}건\n"
            f"  • CPL: ${cpl:.2f}\n\n"
            f"📋 *캠페인별 상세*\n{campaign_section}\n\n"
            f"🏥 *GHL 전환 현황*\n"
            f"  • GHL 유입 리드: {total_ghl_leads}건\n"
            f"  • 예약 완료: {total_bookings}건\n"
            f"  • Lead→Booking CVR: {lead_to_booking_cvr:.1f}%\n\n"
            f"⭐ *핵심 KPI*\n"
            f"  • CPB (Cost/Booking): ${cpb:.2f}\n"
            f"  • ━━━━━━━━━━━━━━━\n"
            f"  • 목표 CPB: $80 이하\n"
        )

        if bot:
            bot.send_message(TELEGRAM_CHAT_ID, report, parse_mode="Markdown")
        log.info(f"[Dashboard] Daily report sent: spend=${total_spend:.2f}, bookings={total_bookings}")
        log.info(report)

    except Exception as e:
        log.error(f"[Dashboard ERROR]: {e}")
        if bot:
            bot.send_message(TELEGRAM_CHAT_ID, f"❌ 일일 리포트 오류: {e}")


def run_daily_report():
    """Scheduler에서 호출되는 동기 wrapper"""
    import asyncio
    asyncio.run(build_daily_report())


# ─────────────────────────────────────────
# 수동 트리거 엔드포인트 (테스트/백필용)
# ─────────────────────────────────────────
@app.post("/admin/trigger-report")
async def trigger_report():
    """일일 리포트 수동 실행"""
    await build_daily_report()
    return {"status": "report sent"}


@app.get("/admin/health")
async def health():
    con = sqlite3.connect(DB_PATH)
    lead_count = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    booking_count = con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    con.close()
    return {
        "status": "ok",
        "leads_synced": lead_count,
        "bookings_tracked": booking_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────
# 스케줄러 설정 (매일 오전 8시 UTC)
# ─────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(run_daily_report, "cron", hour=8, minute=0, id="daily_report")
scheduler.start()

log.info("MedSpa GHL-Meta Bridge 서버 시작됨")
log.info(f"  Webhook Meta Lead : POST /webhook/meta-lead")
log.info(f"  Webhook GHL Book  : POST /webhook/ghl-booking")
log.info(f"  Health Check      : GET  /admin/health")
log.info(f"  Manual Report     : POST /admin/trigger-report")
