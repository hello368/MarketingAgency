# MARTS — Marketing Agency Remote Tracking System
## 최종 기획서 v1.0 | 2026-04-20

---

## 1. 시스템 전체 구조

```mermaid
flowchart TD
    subgraph EXTERNAL["외부 채널"]
        GC["📱 Google Chat\n(12명 팀 채팅)"]
        TG["📲 Telegram\n(알림 전용)"]
        META["📊 Meta Ads Manager\nFacebook / Instagram"]
        GHL["🏢 GoHighLevel CRM\n리드 · 예약 관리"]
    end

    subgraph CORE["MARTS 코어 서버 (FastAPI + uvicorn)"]
        WH["POST /webhook/google-chat\n▸ Bearer JWT 검증\n▸ 즉시 200 반환"]
        NORM["_normalize_gchat_event()\n▸ Format1: Chat App\n▸ Format2: Add-on eventType\n▸ Format3: messagePayload"]
        DISP["_handle_message()\n우선순위 디스패처\n① Task Tracker\n② Check-in\n③ EOD\n④ SLA"]
    end

    subgraph MODULES["처리 모듈"]
        TT["task_tracker.py\n@best 태스크 파싱\n+ 3단계 Nag 에스컬레이션"]
        SLA["SLA Monitor\n15분 응답 타이머\n미응답 → Telegram 알림"]
        CHK["Check-in Parser\n08:55 목표 3개\n09:16 미제출 스윕"]
        EOD["EOD Parser\n16:45 결과 링크\n17:01 미제출 스윕"]
    end

    subgraph STORAGE["데이터 레이어"]
        DB["SQLite\ntracker/data/tracking.db\n▸ members\n▸ checkins\n▸ eod_submissions\n▸ sla_timers\n▸ task_nag_timers\n▸ ping_log\n▸ bot_state"]
        GS["Google Sheets\nMaster Sheet\n▸ Live Status\n▸ Daily Report\n▸ Task_Tracker\n▸ SLA_Log"]
    end

    subgraph AI["AI 엔진 (OpenRouter)"]
        R1["Tier 1\nclaude-sonnet-4-6\n복잡한 분석"]
        R2["Tier 2\ngemini-2.5-pro\n일반 작업"]
        R3["Tier 3\ndeepseek-chat\nfast_reply · 알림"]
    end

    subgraph BRIDGE["GHL-Meta 브릿지"]
        CAPI["Meta CAPI\nConversions API\nevent_id 중복방지\nfbc/fbp EMQ 최적화"]
        WEBHOOK2["GHL Webhook\n리드 수신\n예약 이벤트"]
    end

    GC -->|"HTTP POST\nWorkspace Add-on"| WH
    WH --> NORM --> DISP
    DISP --> TT & SLA & CHK & EOD
    TT & SLA & CHK & EOD --> DB & GS
    SLA -->|"Telegram 알림"| TG
    TT -->|"Nag 에스컬레이션"| TG
    CHK -->|"미체크인 리포트"| TG
    DISP --> AI
    GHL --> WEBHOOK2 --> CAPI --> META
```

---

## 2. 팀 구조 (12명)

```mermaid
flowchart TD
    M["👑 Michael\nTop Leader\n(Agency Owner)"]

    subgraph MO["Management & Ops"]
        KY["Kaye"]
        AN["Anna"]
    end

    subgraph TD2["Tech & Dev"]
        IV["Ivan"]
        IZ["Izzy"]
        KV["Kevin"]
        ML["Milo"]
    end

    subgraph AG["Ads & Growth"]
        TF["Tiffany"]
        DN["Danni"]
    end

    subgraph CR["Creative"]
        SV["Silver"]
        JH["Jhon"]
    end

    subgraph SS["Sales Support"]
        LV["Lovely"]
    end

    M --> MO & TD2 & AG & CR & SS
```

---

## 3. Google Chat 메시지 처리 흐름

```mermaid
sequenceDiagram
    actor 팀원
    participant GChat as Google Chat
    participant Webhook as FastAPI /webhook
    participant Norm as Normalizer
    participant Disp as Dispatcher
    participant DB as SQLite
    participant GS as Google Sheets
    participant TG as Telegram

    팀원->>GChat: 메시지 전송
    GChat->>Webhook: POST (Add-on format)\ncommonEventObject + chat.messagePayload
    Webhook-->>GChat: 200 OK (즉시)
    Webhook->>Norm: _normalize_gchat_event()
    Note over Norm: messagePayload 감지\n→ type="MESSAGE" 강제 설정
    Norm->>Disp: 정규화된 body 전달

    alt @best 포함
        Disp->>DB: task_nag_timer 생성
        Disp->>GS: Task_Tracker 탭 기록
        Disp-->>GChat: 📝 Task registered under [Client] / [City]
    else 체크인 시간 (08:50~09:15)
        Disp->>DB: checkins 기록
        Disp->>GS: Daily Report 탭 기록
    else EOD 시간 (16:45~17:00)
        Disp->>DB: eod_submissions 기록
        Disp->>GS: Daily Report 탭 기록
    else @멘션 포함
        Disp->>DB: sla_timer 생성 (15분 카운트다운)
    end
```

---

## 4. @best 태스크 트래커 파싱 로직

```mermaid
flowchart TD
    IN["원본 텍스트\n예: @best @Michael Kay NYC GlowSpa Q2 덱 검토"]

    S1["Pass 1: <users/ID> 제거"]
    S2["Pass 2: @FirstName(?:\\s+LastName)? 제거\n(다중 단어 이름 처리)"]
    S3["Pass 3: 나머지 @token 제거"]
    CLEAN["정리된 텍스트\n예: NYC GlowSpa Q2 덱 검토"]

    W3{"단어 수 < 3?"}
    GEN["Client = General\nCity = General\nDesc = 전체 텍스트"]
    SKIP{"words[0] 또는\nwords[1] = '-' / '.'?"}
    NORM2["Client = words[0]\nCity = words[1]\nDesc = words[2:]"]
    GENSLOT["해당 슬롯 = General"]

    OUT["📊 Sheets 기록\nClient · City · Description\nAssignee · Thread ID · URLs"]

    IN --> S1 --> S2 --> S3 --> CLEAN
    CLEAN --> W3
    W3 -->|Yes| GEN --> OUT
    W3 -->|No| SKIP
    SKIP -->|Yes| GENSLOT --> OUT
    SKIP -->|No| NORM2 --> OUT
```

---

## 5. Nag 에스컬레이션 타임라인

```mermaid
timeline
    title 태스크 Nag 에스컬레이션 (프로덕션 기준)
    section @best 태스크 생성
        T+0m   : 📝 Task registered 메시지 → Google Chat
               : SQLite task_nag_timers 생성 (assignee별 개별 타이머)
    section Level 1
        T+15m  : 📋 Task Acknowledgment Required
               : Google Chat 스레드에 리마인더
    section Level 2
        T+30m  : ⚠️ Urgent — Task Overdue 30 Minutes
               : Google Chat + Telegram 직접 메시지
    section Level 3
        T+45m  : 🚨 Final Escalation — 45 Minutes
               : Telegram → Michael (관리자) 에스컬레이션
    section 해제
        Any    : Assignee가 스레드에 답변 → acknowledged=1
               : 모든 타이머 즉시 취소
```

---

## 6. SLA 모니터링 흐름

```mermaid
flowchart LR
    TAG["팀원 A가\n팀원 B를 @태그"]
    TIMER["SQLite sla_timers\ndeadline = now + 15분\nresolved=0, pinged=0"]
    POLL["APScheduler\n15초 간격 폴링"]
    CHK2{"deadline\n지났나?"}
    REPLY{"B가 같은 스레드에\n답변했나?"}
    RESOLVE["resolved=1\nSLA_Log: ✅ Met"]
    BREACH["Telegram 알림\n🚨 SLA BREACH\nB → 즉시 답변 촉구\nSLA_Log: ❌ Breach"]

    TAG --> TIMER --> POLL --> CHK2
    CHK2 -->|No| POLL
    CHK2 -->|Yes| BREACH
    REPLY -->|Yes| RESOLVE
    BREACH --> REPLY
```

---

## 7. GHL-Meta 브릿지 데이터 흐름

```mermaid
flowchart TD
    FB["Meta 광고\nFacebook / Instagram"]
    FORM["리드 폼 제출\n이름 · 전화 · 이메일\nfbclid · fbp"]
    GHL2["GoHighLevel CRM\n리드 저장"]
    BRIDGE["ghl_meta_bridge.py"]

    subgraph CAPI2["Meta CAPI 이벤트"]
        LE["Lead 이벤트\nevent_id = evt_Lead_{ts}_{email_hash}\nBUG-M01 Fix"]
        SC["Schedule 이벤트\nfbc = fb.1.{ms}.{fbclid}\nfbp 전달\nBUG-M02 Fix"]
    end

    META2["Meta 이벤트 매니저\nEMQ 점수 향상\n중복 방지"]

    FB -->|"클릭 + fbclid"| FORM
    FORM --> GHL2
    GHL2 -->|"Webhook"| BRIDGE
    BRIDGE --> LE & SC --> META2

    style LE fill:#d4edda
    style SC fill:#d4edda
```

---

## 8. Google Sheets 탭 구조

```mermaid
flowchart LR
    subgraph SHEET["Master Google Spreadsheet"]
        LS["🟢 Live Status\n팀원별 현재 상태\nOnline / Offline / Working"]
        DR["📋 Daily Report\n날짜 · 이름 · 체크인 시간\n목표 1/2/3 · EOD 링크"]
        TK["✅ Task_Tracker\nDate · Thread ID · Client · City\nDescription · Assignee · Status\nRequested At · Completed At\nReference Links · Final Assets"]
        SL["🚨 SLA_Log\nTagger · Tagged · Space\nResolved · Alert Sent · 시간"]
    end
```

---

## 9. 스케줄러 타임라인 (평일 기준, Asia/Manila)

```mermaid
gantt
    title 일일 자동화 스케줄 (PHT UTC+8)
    dateFormat HH:mm
    axisFormat %H:%M

    section 체크인
    체크인 프롬프트 전송       :milestone, 08:55, 0m
    체크인 수신 윈도우         :active, 08:50, 25m
    미체크인 스윕 + Telegram   :milestone, 09:16, 0m

    section EOD
    EOD 프롬프트 전송          :milestone, 16:45, 0m
    EOD 수신 윈도우            :active, 16:45, 15m
    미EOD 스윕 + Telegram      :milestone, 17:01, 0m

    section 상시 폴링
    SLA 타이머 체크 (15초)     :active, 00:00, 1440m
    Nag 타이머 체크 (15초)     :active, 00:00, 1440m
```

---

## 10. 기술 스택 요약

| 레이어 | 기술 | 용도 |
|---|---|---|
| **웹 서버** | FastAPI + uvicorn | Webhook 수신, 즉시 200 반환 |
| **스케줄러** | APScheduler (BackgroundScheduler) | Cron + 15초 interval 폴링 |
| **DB** | SQLite | 팀원 · 체크인 · SLA · 태스크 타이머 |
| **Google Chat** | Workspace Add-on HTTP endpoint | 메시지 수신 (messagePayload 포맷) |
| **Telegram** | pyTelegramBotAPI | 알림 전용 (SLA · Nag · 미체크인) |
| **Google Sheets** | gspread + google-auth | Live Status · Daily · Task · SLA 로그 |
| **AI** | OpenRouter 3-tier | claude-sonnet / gemini / deepseek |
| **Meta CAPI** | requests + SHA256 | 리드·예약 이벤트 전송, EMQ 최적화 |
| **CRM** | GoHighLevel API | 리드·예약 데이터 수신 |
| **인프라** | ngrok (현재) → Cloud Run (예정) | 외부 Webhook 엔드포인트 |

---

## 11. 프로덕션 전환 체크리스트

```mermaid
flowchart TD
    subgraph NOW["✅ 완료"]
        A1["Phase 1: Check-in / EOD 모니터링"]
        A2["Phase 2: SLA 15분 타이머 + Telegram 알림"]
        A3["Phase 3: Google Sheets 실시간 연동"]
        A4["Phase 4: AI 엔진 3-tier 라우팅"]
        A5["Phase 5: @best 태스크 트래커\n+ 3단계 Nag 에스컬레이션"]
        A6["GChat Add-on payload 정규화\n(messagePayload 포맷 지원)"]
        A7["BUG-M01: CAPI event_id 중복방지"]
        A8["BUG-M02: fbclid→fbc EMQ 최적화"]
    end

    subgraph PENDING["🔧 프로덕션 전환 필요"]
        B1["SLA_SECONDS: 15 → 900\n(15분)"]
        B2["NAG_L1/L2/L3: 15/30/45 → 900/1800/2700\n(15/30/45분)"]
        B3["Telegram /register\n11명 미등록 팀원"]
        B4["ngrok → Cloud Run 배포\nDockerfile 준비 완료"]
    end

    subgraph FUTURE["📋 다음 단계"]
        C1["Weekly AI 리포트\n자동 생성 + 슬랙/이메일"]
        C2["Looker Studio 대시보드\nCost per Booking 실시간"]
        C3["TikTok/IG 크리에이티브 수집\n광고 소재 자동화"]
    end

    NOW --> PENDING --> FUTURE
```

---

## 12. 배포 명령어 (프로덕션 전환 시)

```bash
# 1. 타이머 값 변경 (config.py)
SLA_SECONDS    = 900   # 15분
NAG_L1_SECONDS = 900   # 15분
NAG_L2_SECONDS = 1800  # 30분
NAG_L3_SECONDS = 2700  # 45분

# 2. 서버 실행
cd ~/projects/MarketingAgency/tracker
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

# 3. ngrok (임시)
ngrok http --domain=marts-michael.ngrok-free.app 8000

# 4. Cloud Run 배포 (최종)
gcloud run deploy marts-tracker \
  --source ./tracker \
  --region asia-northeast1 \
  --allow-unauthenticated
```

---

*Generated: 2026-04-20 | MARTS v1.0 | claude-sonnet-4-6*
