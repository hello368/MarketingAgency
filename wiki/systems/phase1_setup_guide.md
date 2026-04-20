# Phase 1 Setup Guide — Google Cloud Console + Deployment

> Complete this guide once before running the tracker.
> Estimated time: 45–60 minutes.
> You will need: Google Workspace admin access, a Telegram account.

---

## PART 1: Google Cloud Console

### Step 1.1 — Create a New Project

1. Go to: https://console.cloud.google.com
2. Click the project dropdown (top-left) → **New Project**
3. Name: `agency-tracker`
4. Click **Create**
5. Wait ~30 seconds, then switch to the new project

---

### Step 1.2 — Enable Required APIs

1. In the left menu → **APIs & Services → Library**
2. Search and **Enable** each of these:
   - `Google Chat API`
   - `Google Sheets API`
   - `Google Drive API`
   - `Cloud Run API`
   - `Cloud Build API`

---

### Step 1.3 — Create a Service Account

1. **APIs & Services → Credentials → Create Credentials → Service Account**
2. Name: `agency-tracker-sa`
3. Description: `Agency Tracker service account`
4. Click **Create and Continue**
5. Role: **Editor** (for Sheets + Chat access)
6. Click **Done**
7. Click on the service account you just created
8. Go to **Keys** tab → **Add Key → Create new key → JSON**
9. Download the JSON file
10. **Rename it to `service_account.json`**
11. Place it in: `tracker/credentials/service_account.json`
12. Copy the **service account email** (looks like: `agency-tracker-sa@agency-tracker.iam.gserviceaccount.com`) — you'll need it in Step 2.2

---

## PART 2: Google Chat Bot

### Step 2.1 — Configure the Google Chat API

1. **APIs & Services → Google Chat API → Configuration**
2. Fill in:
   - **App name:** `Agency Tracker`
   - **Avatar URL:** (any icon URL, or leave default)
   - **Description:** `Automated check-in tracking and SLA monitoring`
3. Under **Functionality**:
   - ✅ Receive 1:1 messages
   - ✅ Join spaces and group conversations
4. Under **Connection settings**:
   - Select: **App URL**
   - Enter your webhook URL (fill this in after Step 4 — leave blank for now)
5. Under **Visibility**:
   - Add your Google Workspace domain OR specific users (your 11 team members)
6. Click **Save**

---

### Step 2.2 — Grant Chat API to Service Account

1. In **Google Chat API → Configuration**
2. Under **Service Account**, paste the service account email from Step 1.3
3. Save

---

## PART 3: Google Sheets Master Dashboard

### Step 3.1 — Create the Spreadsheet

1. Go to: https://sheets.google.com
2. Create a **Blank spreadsheet**
3. Name it: `Agency Tracker — Master Dashboard`
4. Copy the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

### Step 3.2 — Share with Service Account

1. Click **Share** (top right)
2. Paste the service account email: `agency-tracker-sa@agency-tracker.iam.gserviceaccount.com`
3. Role: **Editor**
4. Uncheck "Notify people"
5. Click **Share**

### Step 3.3 — (Optional) Share with Michael Only

The app will auto-create the 4 tabs. To restrict access:
1. Remove yourself from sharing (you're the owner — you keep access)
2. Make sure no other team members have the link
3. The dashboard is Michael-only by design

---

## PART 4: Telegram Bot

### Step 4.1 — Create the Bot

1. Open Telegram → Search `@BotFather`
2. Send: `/newbot`
3. Name: `Agency Tracker`
4. Username: `agency_tracker_yourname_bot` (must be unique)
5. BotFather will give you a **bot token** — copy it

### Step 4.2 — Configure .env

```bash
cd tracker/
cp .env.example .env
```

Open `.env` and fill in:
```
TELEGRAM_BOT_TOKEN=1234567890:AAGabcdef...
SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
DAILY_REPORT_SPACE_ID=spaces/XXXXXXXXXXXXX   ← fill after Step 5
```

---

## PART 5: Local Test (Before Cloud Run)

### Step 5.1 — Install Dependencies

```bash
cd tracker/
pip install -r requirements.txt
```

### Step 5.2 — Run Locally with ngrok

```bash
# Terminal 1 — run the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — expose to internet
ngrok http 8000
```

Copy the ngrok HTTPS URL (e.g., `https://abc123.ngrok.io`)

### Step 5.3 — Set Webhook URL in Google Chat

1. Go back to **Google Chat API → Configuration**
2. Under **App URL**: paste `https://abc123.ngrok.io/webhook/google-chat`
3. Save

### Step 5.4 — Add Bot to Daily Report Channel

1. Open Google Chat → Open the **Daily Report Channel** space
2. Click **Add people & apps**
3. Search for **Agency Tracker** (your bot)
4. Add it

### Step 5.5 — Get the Space ID

After adding the bot, watch your server terminal. You'll see a log like:
```
Bot added to space: Daily Report Channel
```

The `ADDED_TO_SPACE` webhook event body will contain `space.name` like `spaces/AAABBBCCC`.

Update `.env`:
```
DAILY_REPORT_SPACE_ID=spaces/AAABBBCCC
```

Restart the server.

### Step 5.6 — Verify Health Check

Open: `http://localhost:8000/health`

You should see:
```json
{
  "status": "ok",
  "members_total": 12,
  "telegram_registered": 0,
  "checkins_today": "0/12"
}
```

---

## PART 6: Team Telegram Registration

**Have Kaye & Anna complete this step with each team member.**

1. Each member opens Telegram
2. Searches for your bot (`@agency_tracker_yourname_bot`)
3. Sends: `/register TheirFirstName`
   - Example: `/register Tiffany`
4. They get a confirmation message

Verify all registrations:
```
http://localhost:8000/health
```
`telegram_registered` should reach 11 (all except Michael if he doesn't need alerts).

---

## PART 7: Deploy to Google Cloud Run

### Step 7.1 — Install Google Cloud CLI

Download: https://cloud.google.com/sdk/docs/install

```bash
gcloud auth login
gcloud config set project agency-tracker
```

### Step 7.2 — Build and Deploy

```bash
cd tracker/

# Build container image
gcloud builds submit --tag gcr.io/agency-tracker/tracker

# Deploy to Cloud Run
gcloud run deploy agency-tracker \
  --image gcr.io/agency-tracker/tracker \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars TELEGRAM_BOT_TOKEN=your_token,SPREADSHEET_ID=your_id,DAILY_REPORT_SPACE_ID=spaces/XXX \
  --set-secrets GOOGLE_CREDENTIALS_PATH=service-account-json:latest
```

**Alternative (simpler): Set env vars via Cloud Console**
1. Go to Cloud Run → agency-tracker → Edit & Deploy New Revision
2. Under **Variables & Secrets** → add all env vars
3. For the service account JSON: upload it as a **Secret** in Secret Manager

### Step 7.3 — Update Webhook URL

After deployment, Cloud Run gives you a URL like:
`https://agency-tracker-abc123-uc.a.run.app`

Update Google Chat App URL to:
`https://agency-tracker-abc123-uc.a.run.app/webhook/google-chat`

---

## PART 8: Final Verification Checklist

- [ ] `/health` endpoint returns `status: ok`
- [ ] Bot is added to **Daily Report Channel**
- [ ] Bot is added to **1.Urgent Accounts** and all other active spaces
- [ ] All 11 team members have sent `/register` in Telegram
- [ ] `telegram_registered` = 11 in health check
- [ ] Send a test message in Daily Report Channel — verify it logs in server console
- [ ] Check Google Sheets — tabs created automatically (Daily Check-ins, EOD Results, SLA Ping Log, Ping Summary)
- [ ] At 08:55 local time — check-in prompt appears in Daily Report Channel
- [ ] Team members reply — check Google Sheets logs the goals
- [ ] At 09:16 — late sweep fires, missing members logged

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Webhook not receiving events | Check ngrok is running, URL is correct in GChat config |
| `Bot added to space` not firing | Remove bot from space, re-add it |
| Google Sheets not updating | Verify service account email has Editor access |
| Telegram alerts not sending | Member hasn't sent `/register` yet |
| Thread key is "not set" at 08:55 | Check DAILY_REPORT_SPACE_ID is correct in .env |
| Cloud Run cold start delays | Set minimum instances to 1 (`--min-instances 1`) |
