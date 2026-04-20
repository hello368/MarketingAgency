# System Architecture Proposal

> System: Remote Tracking & AI Alert System
> Version: v1.0 Draft
> Date: 2026-04-18

---

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    GOOGLE CHAT (Spaces)                      │
│  Team posts check-ins, tags members, replies in threads      │
│                          │                                   │
│               Google Chat Bot (our App)                      │
│               receives all MESSAGE events                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP POST (webhook)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              BACKEND SERVER (Python / FastAPI)               │
│  Hosted on: Google Cloud Run (recommended) or local+ngrok   │
│                                                             │
│  ┌─────────────────┐   ┌──────────────────┐                │
│  │ Check-in Parser │   │  SLA Timer Engine │                │
│  │                 │   │                  │                │
│  │ 08:50-09:15 →  │   │ @mention detected │                │
│  │ extract goals   │   │ → start 15min     │                │
│  │                 │   │   countdown       │                │
│  │ 16:30-17:00 →  │   │ → if no reply     │                │
│  │ extract links   │   │   → fire alert    │                │
│  └────────┬────────┘   └────────┬─────────┘                │
│           │                     │                            │
│           ▼                     ▼                            │
│  ┌──────────────────────────────────────────┐              │
│  │         In-Memory State (+ SQLite)        │              │
│  │  active_timers: {thread_key: timer_data}  │              │
│  │  user_mapping: {google_id: telegram_id}   │              │
│  └────────────────────┬─────────────────────┘              │
└───────────────────────┼─────────────────────────────────────┘
                        │
           ┌────────────┴────────────┐
           ▼                         ▼
┌─────────────────┐       ┌──────────────────────┐
│  GOOGLE SHEETS  │       │    TELEGRAM BOT       │
│  Master Sheet   │       │                       │
│                 │       │  Sends push alert to  │
│  Tab 1: Daily   │       │  tagged member's app  │
│  Check-ins      │       │                       │
│                 │       │  Message format:       │
│  Tab 2: Result  │       │  "🚨 [SLA BREACH]     │
│  Links          │       │   You were tagged by  │
│                 │       │   [Name] 15 min ago   │
│  Tab 3: SLA     │       │   and haven't replied │
│  Ping Log       │       │   Please respond NOW" │
└─────────────────┘       └──────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| **Backend** | Python 3.11 + FastAPI | Async support, familiar (existing bridge.py), fast |
| **Hosting** | Google Cloud Run | Always-on, free tier, stable webhook URL |
| **State Storage** | SQLite (local) + in-memory dict | Simple, no DB setup, survives restarts |
| **Google Chat** | Google Chat API + Service Account | Bot in Spaces, receives all message events |
| **Google Sheets** | Google Sheets API (gspread) | Master Dashboard for Michael |
| **Telegram** | pyTelegramBotAPI | Push alerts to individual members |
| **Scheduler** | APScheduler | Triggers 08:55 prompt, 09:15 late-check sweep, daily report |

---

## Google Sheets: Master Dashboard Structure

### Tab 1: Daily Check-ins
| Date | Name | Group | Check-in Time | Goal 1 | Goal 2 | Goal 3 | Status |
|---|---|---|---|---|---|---|---|
| 2026-04-18 | Tiffany | Ads & Growth | 09:03 | Launch campaign | Review metrics | Client report | ✅ On-time |
| 2026-04-18 | Ivan | Tech & Dev | 09:22 | — | — | — | ⚠️ Late |

### Tab 2: Result Links (16:45 EOD)
| Date | Name | Check-out Time | Link 1 | Link 2 | Link 3 | Status |
|---|---|---|---|---|---|---|
| 2026-04-18 | Tiffany | 16:48 | [URL] | [URL] | — | ✅ Submitted |

### Tab 3: SLA Ping Log
| Date | Time | Space | Tagger | Tagged User | Thread | 15-Min Met? | Telegram Pings Sent |
|---|---|---|---|---|---|---|---|
| 2026-04-18 | 10:23 | General | Michael | Tiffany | [thread_key] | ❌ No | 1 |

### Tab 4: Ping Count Summary (running total)
| Name | Total Pings This Week | Total Pings This Month | Last Breach |
|---|---|---|---|
| Tiffany | 2 | 5 | 2026-04-17 10:23 |

---

## Data Flow: Check-in Detection

```
08:55 UTC+[timezone]: Bot sends prompt message to Space
  "🕘 Good morning team! Time to post your 3 goals for today.
   Format: post 3 numbered goals below."

09:00 - 09:15: Window open
  → Bot receives MESSAGE events from all members
  → Parser checks: sender + timestamp + content has 3 goal-like items
  → Logs to Google Sheets Tab 1

09:15: Sweep
  → Check who has NOT posted
  → Log them as "Late / Missing" in Sheets
  → Optional: send Telegram nudge to missing members
```

---

## Data Flow: 15-Minute SLA Engine

```
Any message received by bot:
  Step 1: Parse for @mentions
    → Extract all mentioned user IDs from message.annotations
    
  Step 2: For each mentioned user:
    → Create timer entry:
       {
         "timer_id": uuid,
         "space": message.space.name,
         "thread_key": message.thread.name,
         "mentioned_user_id": "users/XXXXX",
         "mentioned_user_name": "Tiffany",
         "tagger_name": "Michael",
         "start_time": datetime.utcnow(),
         "deadline": datetime.utcnow() + 15 min,
         "resolved": False
       }
    → Store in active_timers dict
    
  Step 3: Every message in same thread:
    → Check if sender == mentioned_user_id
    → If yes → mark timer resolved = True → log ✅ to Sheets
    
  Step 4: Background job runs every 60 seconds:
    → Check all active_timers where resolved=False and deadline < now
    → For each expired timer:
        a. Send Telegram alert to mentioned_user
        b. Log to Sheets Tab 3 (ping +1)
        c. Mark timer as "alerted" (don't alert again)
```

---

## File Structure (to be built)

```
MarketingAgency/
├── main.py                    ← FastAPI app (webhook receiver)
├── config.py                  ← Team mapping, config constants
├── sla_engine.py              ← 15-minute timer logic
├── checkin_parser.py          ← Check-in/check-out detection
├── sheets_client.py           ← Google Sheets read/write
├── telegram_client.py         ← Telegram alert sender
├── scheduler.py               ← APScheduler jobs (08:55 prompt, sweeps)
├── models.py                  ← Data models (Timer, CheckIn, etc.)
├── data/
│   └── tracking.db            ← SQLite for persistence
├── credentials/
│   ├── google_service_account.json  ← Google API credentials
│   └── .env                   ← Telegram token, Spreadsheet ID, etc.
├── requirements.txt
└── wiki/                      ← This knowledge base
```

---

## Setup Steps (One-Time)

1. **Google Cloud Console**
   - Create project "agency-tracker"
   - Enable: Google Chat API, Google Sheets API, Cloud Run API
   - Create Service Account → download JSON key
   - Create Google Chat Bot → set webhook URL

2. **Google Sheets**
   - Create Master Sheet
   - Share with Service Account email
   - Copy Spreadsheet ID to .env

3. **Telegram**
   - Create bot via @BotFather → get TOKEN
   - Each team member opens bot → sends /start → system logs their chat_id
   - Populate config.py TEAM_MAPPING

4. **Add Bot to Spaces**
   - Add the Google Chat Bot to every relevant Space
   - Verify it receives message events via test post

5. **Deploy**
   - Local: uvicorn main:app + ngrok
   - Cloud: gcloud run deploy

---

## Phase Plan

### Phase 1 (Build First)
- Google Cloud project setup
- FastAPI webhook receiver (validates Google Chat events)
- Check-in parser → writes to Google Sheets
- End-of-day result link detection → writes to Sheets
- Basic Telegram bot (manual test ping)

### Phase 2
- SLA Timer Engine (15-min countdown per @mention)
- Thread-reply resolution logic
- Telegram escalation on breach
- Ping count logging to Sheets

### Phase 3
- 08:55 bot prompt automation
- Late check-in sweep + Telegram nudge
- Tab 4 summary dashboard (weekly/monthly ping counts)
- Edge case handling (multiple mentions, edited messages, bot mentions)
