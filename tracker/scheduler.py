"""
Scheduled Jobs — APScheduler
- 08:55: Send check-in prompt to Daily Report Channel
- 09:16: Sweep for missing check-ins → log + Telegram nudge
- 16:45: Send EOD prompt to Daily Report Channel
- 17:01: Sweep for missing EOD submissions → log
- Every 60s: Check expired SLA timers → fire Telegram alerts
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import db
import state
import sheets_client
import telegram_bot
import ai_engine
from config import (
    TIMEZONE,
    CHECKIN_PROMPT_HOUR, CHECKIN_PROMPT_MINUTE,
    LATE_SWEEP_HOUR, LATE_SWEEP_MINUTE,
    EOD_PROMPT_HOUR, EOD_PROMPT_MINUTE,
    SLA_SECONDS,
)

log = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

DAILY_REPORT_SPACE_ID = os.environ.get("DAILY_REPORT_SPACE_ID", "")

# Lazy import to avoid circular imports
_gchat = None
def _sender():
    global _gchat
    if _gchat is None:
        import gchat_sender as g
        _gchat = g
    return _gchat


# ─────────────────────────────────────────
# JOB 1: 08:55 — Check-in Prompt
# ─────────────────────────────────────────

def job_checkin_prompt():
    log.info("[Scheduler] Sending 08:55 check-in prompt")
    if not DAILY_REPORT_SPACE_ID:
        log.warning("[Scheduler] DAILY_REPORT_SPACE_ID not set — skipping prompt")
        return

    msg = (
        "🕘 *Good morning, team!*\n\n"
        "Time for your daily check-in! 📋\n\n"
        "👇 *Reply to this thread* with your *3 goals for today*:\n\n"
        "1️⃣ Goal 1\n"
        "2️⃣ Goal 2\n"
        "3️⃣ Goal 3\n\n"
        "Check-in closes at *09:15*. Let's have a great day! 💪"
    )
    result = _sender().send_message(DAILY_REPORT_SPACE_ID, msg)
    thread_key = result.get("thread", {}).get("name", "")
    if thread_key:
        db.set_state("checkin_thread_key", thread_key)
        db.set_state("checkin_date", datetime.now(TZ).strftime("%Y-%m-%d"))
        log.info("[Scheduler] Check-in thread key saved: %s", thread_key)
    else:
        log.error("[Scheduler] Failed to get thread key from check-in prompt")


# ─────────────────────────────────────────
# JOB 2: 09:16 — Late Check-in Sweep
# ─────────────────────────────────────────

def job_late_sweep():
    log.info("[Scheduler] Running late check-in sweep")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    missing = db.all_members_today_checkin(today)

    for name in missing:
        log.info("[Scheduler] Missing check-in: %s", name)
        db.log_late_missing(today, name)
        sheets_client.log_late_missing(today, name)

        # Phase 2+: Telegram nudge to missing member
        # telegram_bot.send_alert(name, f"⚠️ *Check-in reminder*\nYou haven't posted your daily goals yet!\nPlease post in the Daily Report Channel.")

    if missing:
        michael = db.get_member_by_name("Michael")
        if michael and michael["telegram_chat_id"]:
            names_list = "\n".join(f"• {n}" for n in missing)
            # AI generates the summary; fallback to template if unavailable
            ai_text = ai_engine.chat(
                f"Write a concise 2-line Telegram report for the agency manager. "
                f"{len(missing)} team member(s) missed their morning check-in today ({today}). "
                f"Missing: {', '.join(missing)}. Factual and direct. No emojis needed beyond the header.",
                task_type="fast_reply",
            )
            msg = ai_text or (
                f"📋 *Missing Check-ins ({today})*\n\n{names_list}\n\nLogged in Master Sheet."
            )
            telegram_bot.send_direct(michael["telegram_chat_id"], msg)
    log.info("[Scheduler] Late sweep complete — %d missing", len(missing))


# ─────────────────────────────────────────
# JOB 3: 16:45 — EOD Prompt
# ─────────────────────────────────────────

def job_eod_prompt():
    log.info("[Scheduler] Sending 16:45 EOD prompt")
    if not DAILY_REPORT_SPACE_ID:
        log.warning("[Scheduler] DAILY_REPORT_SPACE_ID not set — skipping EOD prompt")
        return

    msg = (
        "🕔 *EOD Check-out Time!*\n\n"
        "Great work today, team! 🎉\n\n"
        "👇 *Reply to this thread* with your *result links* for today:\n\n"
        "📎 Paste your deliverable links below (Google Docs, Sheets, Drive, etc.)\n\n"
        "Deadline: *17:00*. Don't forget! ⏰"
    )
    result = _sender().send_message(DAILY_REPORT_SPACE_ID, msg)
    thread_key = result.get("thread", {}).get("name", "")
    if thread_key:
        db.set_state("eod_thread_key", thread_key)
        log.info("[Scheduler] EOD thread key saved: %s", thread_key)
    else:
        log.error("[Scheduler] Failed to get thread key from EOD prompt")


# ─────────────────────────────────────────
# JOB 4: 17:01 — Missing EOD Sweep
# ─────────────────────────────────────────

def job_eod_sweep():
    log.info("[Scheduler] Running EOD submission sweep")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    con = db.get_conn()
    submitted = {
        row[0]
        for row in con.execute(
            "SELECT member_name FROM eod_submissions WHERE date=?", (today,)
        ).fetchall()
    }
    all_names = {row[0] for row in con.execute("SELECT name FROM members").fetchall()}
    con.close()
    missing_eod = sorted(all_names - submitted)

    for name in missing_eod:
        log.info("[Scheduler] Missing EOD: %s", name)
        con2 = db.get_conn()
        con2.execute(
            "INSERT OR IGNORE INTO eod_submissions (date, member_name, status) VALUES (?,?,'missing')",
            (today, name)
        )
        con2.commit()
        con2.close()

    if missing_eod:
        michael = db.get_member_by_name("Michael")
        if michael and michael["telegram_chat_id"]:
            names_list = "\n".join(f"• {n}" for n in missing_eod)
            ai_text = ai_engine.chat(
                f"Write a concise 2-line Telegram report for the agency manager. "
                f"{len(missing_eod)} team member(s) failed to submit their EOD results by 17:00 on {today}. "
                f"Missing: {', '.join(missing_eod)}. Factual and direct.",
                task_type="fast_reply",
            )
            msg = ai_text or (
                f"📋 *Missing EOD Submissions ({today})*\n\n{names_list}\n\nLogged in Master Sheet."
            )
            telegram_bot.send_direct(michael["telegram_chat_id"], msg)
    log.info("[Scheduler] EOD sweep complete — %d missing", len(missing_eod))


# ─────────────────────────────────────────
# JOB 5: Every 60s — SLA Timer Check (Phase 2)
# Included here so Phase 2 just uncomments this block.
# ─────────────────────────────────────────

def job_sla_check():
    """Check for expired SLA timers and fire Telegram alerts."""
    expired = db.get_expired_sla_timers()
    for timer in expired:
        name   = timer["tagged_name"]
        tagger = timer["tagger_name"]
        space  = timer["space_display"]

        # AI generates a direct, urgent alert (fast_reply → deepseek/deepseek-chat)
        ai_text = ai_engine.chat(
            f"Write a 2-line urgent Telegram alert for a remote team member. "
            f"They were @mentioned by {tagger} in the '{space}' Google Chat channel "
            f"and have not replied within the 15-minute SLA window. "
            f"Start with 🚨 [SLA BREACH]. Direct and professional. No extra explanation.",
            task_type="fast_reply",
        )
        alert = ai_text or (
            f"🚨 *[SLA BREACH]* {name}, you were tagged by *{tagger}* "
            f"in *{space}* and missed the 15-minute reply window.\n"
            f"Please respond immediately!"
        )
        sent = telegram_bot.send_alert(name, alert)
        db.mark_sla_pinged(timer["id"])
        db.log_ping(name, "SLA breach", timer["space_name"], timer["thread_key"])

        # Log breach to GSheet SLA Log tab via shared state (no circular import)
        if state.gsheet:
            try:
                state.gsheet.log_sla_breach(
                    tagger=tagger,
                    tagged=name,
                    space=space,
                    resolved=False,
                    alert_sent=sent,
                )
            except Exception as e:
                log.warning("[SLA] GSheet log skipped: %s", e)

        log.info("[SLA] 🚨 Alert fired — %s tagged by %s in '%s' (timer_id=%s, sent=%s)",
                 name, tagger, space, timer["id"], sent)


# ─────────────────────────────────────────
# Scheduler Setup
# ─────────────────────────────────────────

def job_task_nag_check():
    """Check for expired task nag timers and fire escalation alerts."""
    import task_tracker
    task_tracker.check_and_fire_nag_alerts()


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ)

    scheduler.add_job(
        job_checkin_prompt, CronTrigger(
            hour=CHECKIN_PROMPT_HOUR, minute=CHECKIN_PROMPT_MINUTE, timezone=TZ
        ), id="checkin_prompt", replace_existing=True
    )
    scheduler.add_job(
        job_late_sweep, CronTrigger(
            hour=LATE_SWEEP_HOUR, minute=LATE_SWEEP_MINUTE, timezone=TZ
        ), id="late_sweep", replace_existing=True
    )
    scheduler.add_job(
        job_eod_prompt, CronTrigger(
            hour=EOD_PROMPT_HOUR, minute=EOD_PROMPT_MINUTE, timezone=TZ
        ), id="eod_prompt", replace_existing=True
    )
    scheduler.add_job(
        job_eod_sweep, CronTrigger(
            hour=17, minute=1, timezone=TZ
        ), id="eod_sweep", replace_existing=True
    )
    # SLA check — 15s interval balances responsiveness against SQLite contention.
    scheduler.add_job(
        job_sla_check, "interval", seconds=15, id="sla_check", replace_existing=True
    )
    # Task nag check — same 15s cadence, separate job for clean separation.
    scheduler.add_job(
        job_task_nag_check, "interval", seconds=15, id="task_nag_check", replace_existing=True
    )

    return scheduler
