# MARTS — ngrok Wiring Guide
# Connecting the Local Server to Google Chat

> **Goal:** Get a live HTTPS URL from ngrok so Google Chat can deliver webhook
> events to your local server. Do this once per session (or use the fixed domain
> so the URL never changes).
>
> **Status as of 2026-04-19:** Phase 3 engine built and tested locally.
> This step activates real Google Chat → Telegram escalation.

---

## Overview: What ngrok Does

```
Google Chat
    │
    │  POST /webhook/google-chat
    ▼
ngrok HTTPS tunnel  (e.g. https://marts-michael.ngrok-free.app)
    │
    ▼
Your PC  →  WSL  →  uvicorn on localhost:8000
    │
    ├── SLA Timer starts
    ├── 15s later → Telegram alert fires
    └── Google Sheet SLA Log updated
```

---

## PART 1: Run ngrok (Windows Terminal / PowerShell)

Open a **Windows** terminal (NOT WSL). Run these two commands:

### Step 1.1 — Register your authtoken (one time only)

```powershell
ngrok config add-authtoken YOUR_TOKEN_HERE
```

### Step 1.2 — Start tunnel on your fixed domain

```powershell
ngrok http --domain=YOUR_FIXED_DOMAIN_HERE 8000
```

Replace `YOUR_FIXED_DOMAIN_HERE` with the exact domain shown in your
ngrok dashboard (e.g. `marts-michael.ngrok-free.app`).

You will see output like:
```
Session Status    online
Account           Michael (Plan: Free)
Version           3.x.x
Region            Asia Pacific (ap)
Forwarding        https://YOUR_FIXED_DOMAIN_HERE -> http://localhost:8000
```

Your public webhook URL is now:
```
https://YOUR_FIXED_DOMAIN_HERE/webhook/google-chat
```

**Leave this terminal open.** If you close it, the tunnel dies.

---

## PART 2: Run the FastAPI Server (WSL Terminal)

Open a **separate WSL terminal**:

```bash
cd /mnt/c/Users/hsnam/projects/MarketingAgency/tracker
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Wait for:
```
[Telegram] Bot identity confirmed: @Michael_Agency_Bot
[GSheet] ✅ Connectivity test passed
Application startup complete.
```

---

## PART 3: Update Google Chat API Webhook URL

1. Go to: https://console.cloud.google.com
2. Navigate to: **APIs & Services → Google Chat API → Configuration**
3. Under **Connection settings → App URL**, paste:
   ```
   https://YOUR_FIXED_DOMAIN_HERE/webhook/google-chat
   ```
4. Click **Save**

---

## PART 4: Add the Bot to Your Google Chat Spaces

The bot must be added to every space it should monitor.

### Required Spaces (add bot to all of these):
- **Daily Report Channel** — for check-in/EOD prompts
- **1.Urgent Accounts** — or any space where @mentions happen

### How to add:
1. Open the space in Google Chat
2. Click the space name at the top → **Add people & apps**
3. Search: **Agency Tracker**
4. Click **Add**

### What you'll see in the WSL terminal:
```
INFO [GChat] Bot added to space: Daily Report Channel
```

---

## PART 5: Capture the Space ID for Daily Report Channel

When the bot is added to a space, it fires an `ADDED_TO_SPACE` webhook event.
The server log will show the space name.

But to get the exact `space.name` (like `spaces/AAABBBCCC`), watch the terminal
or temporarily add a print — easier: send any message in the space after adding
the bot. The server log will show:

```
INFO [GChat] Message from [Name] in 'Daily Report Channel'
```

To see the raw space ID, check the `/health` endpoint or look at the
`ADDED_TO_SPACE` event body in the terminal JSON output.

**Update `.env`:**

```
DAILY_REPORT_SPACE_ID=spaces/AAABBBCCC
```

Then restart the server.

---

## PART 6: End-to-End Live Test

### Test 1 — Bot responds to being added
- Add the bot to a space
- It should reply: `✅ Agency Tracker is now active in this space.`

### Test 2 — SLA Timer fires via real Google Chat @mention

1. In any Google Chat space where the bot is added, type:
   ```
   @Michael test mention
   ```
2. Watch the WSL terminal:
   ```
   [GChat] Received message in thread spaces/... mentioning Michael
   SLA timer started (15s), deadline HH:MM:SS [timer_id=X]
   ```
3. Wait 15 seconds
4. Your Telegram phone receives:
   ```
   🚨 [URGENT] You were tagged by [Sender] in [Space]. You missed the SLA. Please respond immediately!
   ```
5. Open Google Sheet → SLA Log tab → new row appears

### Test 3 — Cancellation (reply within 15 seconds)
- Within 15s of the @mention, reply in the **same thread**
- Terminal shows: `[SLA] ✅ Timer resolved — Michael replied`
- No Telegram alert fires
- Sheet logs `Resolved? = ✅ Yes`

---

## PART 7: Flip to Production (15 minutes)

When everything works, change one line in `config.py`:

```python
SLA_SECONDS = 900   # 15 minutes — production mode
```

Restart the server. The SLA check still runs every 5 seconds (lightweight DB query),
but the deadline is now 15 minutes instead of 15 seconds.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Google Chat not reaching webhook | Verify ngrok is running and App URL ends with `/webhook/google-chat` |
| `401 Missing auth token` in logs | Google Chat sends a Bearer JWT — this is correct behavior, not an error |
| Bot added but no log in terminal | Check ngrok is forwarding to port 8000, not another port |
| `DAILY_REPORT_SPACE_ID not set` warning | Update `.env` with the correct space ID and restart |
| SLA timer fires but Telegram silent | Member hasn't sent `/register` in Telegram yet |
| ngrok session expired | Re-run `ngrok http --domain=... 8000` in Windows terminal |
