# Technical Review & Bottleneck Analysis

> System: Remote Tracking & AI Alert System
> Reviewer: Claude (AI System Architect)
> Date: 2026-04-18

---

## Critical Bottleneck #1: Google Chat Cannot Be Passively Monitored

**The Problem:**
Google Chat does not have a "listen to all messages" API like Slack's Events API. You cannot simply subscribe to a Space and read all messages. The only way to receive real-time message events from Google Chat is to **deploy a Google Chat Bot (App)** that is a member of the Space.

**What This Means:**
- A Google Chat Bot must be created via Google Cloud Console
- The bot must be **manually added to every Space** where check-ins and @mentions happen
- The bot receives MESSAGE events whenever someone posts — including @mentions of other users
- Without this bot being present in the Space, the system is blind

**Implication for Setup:**
Michael (or a Workspace admin) must add the bot to all relevant Google Chat Spaces once during setup. This is a one-time step but requires Google Workspace admin privileges to create the bot.

---

## Critical Bottleneck #2: The 15-Minute Thread-Reply Problem

**The Problem:**
The Golden Rule requires knowing if **Person B (who was tagged)** replied **in the same thread** within 15 minutes. This is technically non-trivial.

**How Google Chat threads work:**
- Every message has a `thread.name` field (unique thread identifier)
- A reply in a thread has the same `thread.name` as the original message
- Our bot receives ALL message events, including thread replies

**The Timer Logic Required:**
```
1. Bot receives message: "Hey @Tiffany, check this" → thread_key = "spaces/XXX/threads/YYY"
2. System parses @mention → identifies Tiffany's user ID
3. System creates a timer: {user: Tiffany, thread: YYY, deadline: now + 15 min}
4. All subsequent messages are checked: 
   - Same thread? → Same thread_key
   - From Tiffany? → sender.name matches Tiffany's ID
   - If YES → cancel timer (SLA met)
   - If timer expires → fire Telegram alert + log to Sheets
```

**Edge Case: Multiple People Tagged**
"Hey @Ivan @Kevin, can you review this?" → Two separate 15-min timers needed, each cancelled independently.

**Edge Case: Mention in a New Thread vs. Reply Thread**
If Michael tags Tiffany in a direct new message (not a reply), the thread_key is the message's own thread. Any reply to that message creates a thread. We need to track the original message's thread_key and monitor for Tiffany's reply.

---

## Critical Bottleneck #3: Telegram User ID Mapping

**The Problem:**
Telegram identifies users by `chat_id` (a unique number). Google Chat identifies users by their Google account (`users/XXXXX`). These two systems have no native connection.

**Required Setup (One-Time Manual Step):**
1. Michael creates a Telegram Bot
2. Each team member must open the bot and send `/start`
3. The bot logs each member's Telegram `chat_id`
4. A mapping table is created:

```python
TEAM_MAPPING = {
    "users/12345678": {"name": "Tiffany", "telegram_id": 987654321},
    "users/87654321": {"name": "Ivan",    "telegram_id": 123456789},
    # ... all 11 members
}
```

**Risk:** If a team member doesn't `/start` the bot, they cannot receive escalation alerts. This must be completed as part of onboarding.

---

## Bottleneck #4: Check-in Format Standardization

**The Problem:**
"Posts 3 daily goals at 09:00" — what format? If each person writes differently, parsing is unreliable.

**Options:**
- **Option A (Recommended): Enforce a template** — The bot sends a prompt to the Space at 08:55: "🕘 Check-in time! Please post your 3 goals using this format: `[GOAL 1] [GOAL 2] [GOAL 3]`"
- **Option B: NLP parsing** — Use AI to detect "this looks like a check-in post" — more flexible but error-prone
- **Option C: Structured slash command** — `/checkin Goal1 | Goal2 | Goal3` — most reliable, least friction

**Recommendation:** Option A (prompted template) for day 1. Upgrade to slash commands later.

**Time Window for Check-in:**
Strict 09:00 is impractical remotely. Recommend: 08:50–09:15 window counts as on-time. After 09:15 = logged as "Late Check-in" in Master Sheet.

---

## Bottleneck #5: Local PC Reliability

**The Problem:**
Running the backend server on Michael's PC means:
- If the PC sleeps/restarts → system goes down → SLA timers are lost
- If internet drops → webhook events are missed
- ngrok free tier changes URL on restart → webhook must be re-registered

**Recommended Solution: Google Cloud Run (Free Tier)**
- Always on, serverless
- Free tier: 2 million requests/month (more than enough)
- Stable HTTPS URL → webhook registration is permanent
- Costs: $0 for this scale
- Michael's PC is only used for development, not as the server

**Alternative if Cloud is not preferred:** A cheap always-on VPS ($5/month, DigitalOcean or Hetzner) or a dedicated mini-PC (Raspberry Pi 4).

---

## Bottleneck #6: Google Chat API Rate Limits

- Google Chat API: 2,500 requests/day per project (free tier)
- At 12 members × multiple messages/day: well within limits
- Sheets API: 300 write requests/minute → not an issue
- **No rate limit concern at this team size**

---

## Summary Table

| # | Issue | Severity | Resolution |
|---|---|---|---|
| 1 | Bot must be in every Space | Critical | One-time setup by admin |
| 2 | Thread-reply timer logic | Critical | State machine with thread_key tracking |
| 3 | Telegram ID mapping | High | Manual `/start` by each member |
| 4 | Check-in format inconsistency | Medium | Bot-prompted template at 08:55 |
| 5 | Local PC reliability | High | Use Google Cloud Run instead |
| 6 | API rate limits | Low | Non-issue at 12-member scale |
