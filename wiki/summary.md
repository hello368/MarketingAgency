# Agency Remote Tracking & AI Alert System

> **Project Owner:** Michael (CEO)
> **Last Updated:** 2026-04-18
> **Status:** Webhook Debugging — Fixed auth 401 + live_status bug, raw logging added

---

## 1. Organization (12 Members)

| # | Name | Role | Group |
|---|---|---|---|
| 1 | **Michael** | CEO / Chief Closer / Sales & Strategy | Top Leader |
| 2 | **Kaye** | Lead PM, Payment Services, WhatsApp | Management & Ops |
| 3 | **Anna** | Ops Assistant, WhatsApp | Management & Ops |
| 4 | **Ivan** | Lead Developer | Tech & Dev |
| 5 | **Izzy** | QA / Support | Tech & Dev |
| 6 | **Kevin** | Web Developer | Tech & Dev |
| 7 | **Milo** | Execution | Tech & Dev |
| 8 | **Tiffany** | Facebook Ads Lead | Ads & Growth |
| 9 | **Danni** | Ads Assistant | Ads & Growth |
| 10 | **Silver** | Video Creator / Trend Analyst | Creative |
| 11 | **Jhon** | Canva Designer | Creative |
| 12 | **Lovely** | MedSpa CRM & VIP Scheduling | Sales Support |

**Hours:** 09:00–17:00 (no lunch break, fully remote)

---

## 2. Core SOP Rules

### Rule 1: Daily Check-in/out
- **09:00** — Each member posts 3 daily goals in Google Chat Space
- **16:45** — Each member posts final result links in Google Chat Space

### Rule 2: The Golden Rule (15-Minute SLA)
- Any @mention in Google Chat → tagged member **must reply within 15 minutes**
- Failure = emergency escalation via Telegram

---

## 3. Decisions Locked

| # | Decision | Choice |
|---|---|---|
| 1 | Hosting | Google Cloud Run (free tier) |
| 2 | Check-in format | Option A — bot sends 08:55 prompt, team replies in thread |
| 3 | Telegram mapping | `/register [Name]` command, managed by Kaye & Anna |
| 4 | Space coverage | Bot listens to ALL spaces it is invited to |

---

## 4. Build Phases

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Google Cloud + Bot + Check-in/EOD logging + Google Sheets + Telegram registration | ✅ **LIVE** |
| **Phase 2** | Telegram /register, webhook @mention logging, /test-webhook endpoint | ✅ **CODE COMPLETE** |
| **Phase 3** | 15-sec SLA timer engine, Telegram escalation, SLA Log tab, /test-reply endpoint | ✅ **CODE COMPLETE** |

---

## 5. Phase 1 — File Structure

```
tracker/
├── main.py              ← FastAPI app — webhook receiver, event router
├── config.py            ← Team members, timezone, time windows
├── db.py                ← SQLite layer — all DB reads/writes
├── checkin_parser.py    ← Extracts 3 goals from free-form text, URLs from EOD
├── sheets_client.py     ← Google Sheets — 4-tab Master Dashboard
├── gchat_sender.py      ← Google Chat REST API — sends proactive messages
├── telegram_bot.py      ← Telegram bot — /register command + send_alert()
├── scheduler.py         ← APScheduler — 08:55 prompt, 09:16 sweep, 16:45 EOD, SLA check
├── requirements.txt
├── Dockerfile           ← Cloud Run deployment
├── .env.example
├── data/                ← SQLite DB (auto-created)
└── credentials/
    └── service_account.json  ← Google API credentials (never commit)
```

---

## 6. Master Google Sheet — 5 Tabs

| Tab | Tracks |
|---|---|
| **Live Status** | A=Name, B=Current Status (emoji), C=Last Active, D=Telegram Pings, E=Notes |
| **Daily Check-ins** | Date, Name, Group, Time, Goal 1/2/3, On-time/Late/Missing |
| **EOD Results** | Date, Name, Group, Time, Link 1/2/3, Submitted/Missing |
| **SLA Ping Log** | Date, Space, Tagger, Tagged, Thread, 15-Min Met?, Telegram Pings |
| **Ping Summary** | Name, Group, Weekly Pings, Monthly Pings, Total, Last Breach |

---

## 7. Scheduler Jobs

| Time (Local) | Job | Action |
|---|---|---|
| 08:55 | Check-in Prompt | Bot posts prompt in Daily Report Channel |
| 09:16 | Late Sweep | Logs missing members → Telegram alert to Michael |
| 16:45 | EOD Prompt | Bot posts EOD prompt in Daily Report Channel |
| 17:01 | EOD Sweep | Logs missing submissions → Telegram alert to Michael |
| Every 60s | SLA Check | Fires Telegram alert if 15-min timer expired |

---

## 8. Key Wiki Documents

| File | Content |
|---|---|
| `systems/system_architecture.md` | Full architecture diagrams + data flows |
| `systems/technical_review.md` | 6 bottlenecks + resolutions |
| `systems/phase1_setup_guide.md` | Step-by-step Google Cloud Console setup |
| `systems/ngrok_wiring_guide.md` | **ACTIVE** — ngrok setup + Google Chat webhook wiring |
