# GHL × Meta API 자동화 전략

> **북극성 지표:** Cost per Booking (CPB) & Show Rate — 허영 지표(노출, 클릭)가 아닌 실제 예약 기준으로 모든 자동화를 설계한다.

---

## 1. 시스템 아키텍처 전체 그림

```
┌─────────────────────────────────────────────────────────────┐
│                        META (Facebook/Instagram)            │
│  Lead Form 제출 → Webhook ──────────────────────────┐       │
│  Conversions API ←── Booking 이벤트 수신 ───────────┤       │
│  Ads Insights API → 일일 지표 Pull ────────────────┐│       │
└─────────────────────────────────────────────────────┼┼───────┘
                                                      ││
                        ┌─────────────────────────────┼┼───────┐
                        │     GHL_META_BRIDGE (FastAPI)││       │
                        │  /webhook/meta-lead  ←───────┘│       │
                        │  /webhook/ghl-booking ←────────┘       │
                        │  /scheduler (APScheduler) daily sync   │
                        │  SQLite DB (local cache)               │
                        └─────────────────────────────┬──────────┘
                                                      │
┌─────────────────────────────────────────────────────┼───────┐
│                     GoHighLevel (GHL)               │       │
│  Contact 생성/업데이트 ←──────────────────────────────┘       │
│  Opportunity (Pipeline) 업데이트                             │
│  Appointment Webhook → GHL_META_BRIDGE                      │
└─────────────────────────────────────────────────────────────┘
                              │
                   ┌──────────▼──────────┐
                   │   Daily Dashboard   │
                   │  (Telegram 리포트)   │
                   └─────────────────────┘
```

---

## 2. 핵심 자동화 Flow 3가지

### Flow 1: Meta Lead → GHL Contact 즉시 동기화

**트리거:** Meta Lead Ads 폼 제출 (실시간 Webhook)

**단계:**
```
Meta Lead Form 제출
  └→ Meta Webhooks (Leadgen 이벤트) 발송
       └→ /webhook/meta-lead 수신
            └→ GHL Contacts API POST
                 └→ GHL Workflow 시작 (SMS/이메일 즉시 발송)
                      └→ 예약 전환 추적 시작
```

**GHL에 매핑할 필드:**
| Meta Form 필드 | GHL 필드 | 비고 |
|---|---|---|
| full_name | firstName + lastName | 공백 기준 split |
| email | email | |
| phone_number | phone | E.164 포맷 변환 |
| treatment_interest | customField.treatment | |
| ad_id | customField.meta_ad_id | Attribution용 |
| campaign_id | customField.meta_campaign_id | |
| ad_set_id | customField.meta_adset_id | |
| form_id | customField.meta_form_id | |
| created_time | customField.lead_timestamp | |

**SLA:** Lead Form 제출 후 5분 이내 GHL 등록 + 첫 SMS 발송 목표.

---

### Flow 2: GHL Booking → Meta CAPI (전환 최적화)

**트리거:** GHL Appointment 생성 Webhook

**단계:**
```
GHL 예약 완료 (Appointment Created)
  └→ /webhook/ghl-booking 수신
       └→ Lead DB에서 meta_ad_id 매핑 조회
            └→ Meta Conversions API POST
                 └→ 이벤트: "Schedule" (event_name)
                      └→ Meta 알고리즘이 "예약하는 유저" 학습
```

**Meta CAPI 페이로드:**
```json
{
  "event_name": "Schedule",
  "event_time": 1700000000,
  "action_source": "crm",
  "user_data": {
    "em": ["<hashed_email>"],
    "ph": ["<hashed_phone>"],
    "fn": ["<hashed_first_name>"],
    "ln": ["<hashed_last_name>"]
  },
  "custom_data": {
    "value": 150.00,
    "currency": "USD",
    "treatment_type": "Botox"
  }
}
```

**왜 중요한가:** Meta가 "예약"을 목표로 캠페인을 최적화하게 됨 → CPL 낮추는 것보다 CPB 낮추는 방향으로 알고리즘 학습.

---

### Flow 3: 일일 퍼포먼스 대시보드 자동 생성

**트리거:** 매일 오전 8시 (Scheduler)

**데이터 수집:**
```
Meta Insights API
  └→ 계정별 Spend, Impressions, Clicks, Lead 수, CPL
       └→ 캠페인별 상세 breakdown

GHL API
  └→ 어제 생성된 Contact 수 (= 실제 GHL 유입 리드)
  └→ 어제 생성된 Appointment 수 (= 예약 전환)
  └→ Pipeline Stage 분포 (리드 → 상담 → 예약 → 방문)
```

**계산 지표:**
| 지표 | 공식 |
|---|---|
| CPL (Cost per Lead) | Spend ÷ Meta Leads |
| CPB (Cost per Booking) | Spend ÷ GHL Bookings |
| Lead → Booking CVR | GHL Bookings ÷ GHL Contacts × 100% |
| Show Rate | 실제 방문 ÷ GHL Bookings × 100% |
| ROAS (추정) | (Bookings × Avg Ticket) ÷ Spend |

**출력:** Telegram 메시지로 일일 스냅샷 발송

---

## 3. API 인증 구조

### Meta API
- **방식:** Bearer Token (Long-Lived User Access Token 또는 System User Token)
- **필요 권한:** `ads_read`, `ads_management`, `leads_retrieval`, `pages_read_engagement`
- **Webhooks:** Facebook App → Webhooks 설정 → leadgen 이벤트 구독
- **CAPI:** `https://graph.facebook.com/v19.0/{pixel_id}/events`

### GoHighLevel API
- **방식:** Bearer Token (GHL Agency API Key 또는 Location API Key)
- **Base URL:** `https://services.leadconnectorhq.com`
- **Webhooks:** GHL Settings → Webhooks → Appointment Created 등록
- **필요 endpoints:**
  - `POST /contacts/` — 리드 생성
  - `PUT /contacts/{id}` — 리드 업데이트
  - `GET /contacts/` — 리드 조회
  - `GET /appointments/` — 예약 조회
  - `GET /opportunities/` — 파이프라인 조회

---

## 4. 환경 변수 (.env)

```env
# Meta
META_ACCESS_TOKEN=your_long_lived_token
META_AD_ACCOUNT_ID=act_XXXXXXXXXX
META_PIXEL_ID=XXXXXXXXXXXXXXXXX
META_VERIFY_TOKEN=your_webhook_verify_secret

# GoHighLevel
GHL_API_KEY=your_ghl_api_key
GHL_LOCATION_ID=your_location_id

# Telegram (기존 bridge 연동)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Server
PORT=8000
DB_PATH=./data/medspa_bridge.db
```

---

## 5. 구현 파일 구조

```
scripts/
  ghl_meta_bridge.py     ← 메인 서버 (FastAPI + Scheduler)
  
data/
  medspa_bridge.db       ← SQLite (리드-예약 매핑 캐시)

docs/
  ghl_meta_automation_strategy.md   ← 이 문서

.env                     ← API 키 (gitignore 필수)
.env.example             ← 키 없는 템플릿
```

---

## 6. 구현 우선순위 (Phase)

| Phase | 작업 | 기대 효과 |
|---|---|---|
| **Phase 1** | Meta Webhook → GHL Contact 동기화 | 리드 유실 0%, 5분 이내 첫 터치 |
| **Phase 2** | GHL Booking → Meta CAPI | 알고리즘 최적화로 CPB 20~30% 개선 |
| **Phase 3** | 일일 대시보드 자동화 | 수동 리포팅 완전 제거 |
| **Phase 4** | 주간/월간 리포트 생성 | 클라이언트 리텐션 강화 |

---

## 7. 리스크 및 대응

| 리스크 | 대응 |
|---|---|
| Meta Webhook 누락 | 1시간마다 `/leads` API Pull 백업 동기화 |
| GHL API Rate Limit | 요청 간 딜레이 + 지수 백오프 재시도 |
| CAPI 해싱 오류 | SHA-256 해싱 함수 유닛 테스트 필수 |
| 토큰 만료 | Long-Lived Token (60일) + 만료 7일 전 알림 |
