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


def job_focus_check():
    """Check focus mode timers: fire 45-min check-in or resume nag alerts on no-reply."""
    import task_tracker
    task_tracker.check_and_fire_focus_checks()


# ─────────────────────────────────────────
# JOB 8: Every Friday 17:00 — Weekly AI Report
# ─────────────────────────────────────────

def _date_range_this_week() -> tuple[str, str, str]:
    """Return (week_label, date_from, date_to) for the Mon–Fri week ending today."""
    import datetime as _dt
    now = datetime.now(TZ)
    monday = now - _dt.timedelta(days=now.weekday())
    friday = monday + _dt.timedelta(days=4)
    label = f"W{monday.strftime('%Y-%m-%d')}"
    return label, monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")


def job_weekly_report(overwrite: bool = False):
    """Aggregate the week's tracking data, generate AI narrative, send to Michael on Telegram.

    Staged execution (each stage independent):
      1. DB aggregation   — abort on failure
      2. Sheet recording  — continue on failure (logged)
      3. AI report gen    — abort on failure
      4. Telegram send    — 3 retries with exponential backoff
    """
    import time as _time
    log.info("[Scheduler] Running weekly report job (overwrite=%s)", overwrite)
    week_label, date_from, date_to = _date_range_this_week()

    michael = db.get_member_by_name("Michael")

    def _alert_michael(msg: str):
        if michael and michael["telegram_chat_id"]:
            telegram_bot.send_direct(michael["telegram_chat_id"], msg)

    # ── Stage 1: DB aggregation ───────────────────────────────────────────────
    try:
        con = db.get_conn()

        checkins_ontime  = con.execute(
            "SELECT COUNT(*) FROM checkins WHERE date BETWEEN ? AND ? AND status='on-time'",
            (date_from, date_to)
        ).fetchone()[0]
        checkins_late    = con.execute(
            "SELECT COUNT(*) FROM checkins WHERE date BETWEEN ? AND ? AND status='late'",
            (date_from, date_to)
        ).fetchone()[0]
        checkins_missing = con.execute(
            "SELECT COUNT(*) FROM checkins WHERE date BETWEEN ? AND ? AND status='missing'",
            (date_from, date_to)
        ).fetchone()[0]

        eods_submitted = con.execute(
            "SELECT COUNT(*) FROM eod_submissions WHERE date BETWEEN ? AND ? AND status='submitted'",
            (date_from, date_to)
        ).fetchone()[0]
        eods_missing   = con.execute(
            "SELECT COUNT(*) FROM eod_submissions WHERE date BETWEEN ? AND ? AND status='missing'",
            (date_from, date_to)
        ).fetchone()[0]

        sla_breaches = con.execute(
            "SELECT COUNT(*) FROM sla_timers WHERE telegram_pinged=1 AND created_at >= ? AND created_at <= ?",
            (date_from + "T00:00:00", date_to + "T23:59:59")
        ).fetchone()[0]
        sla_met = con.execute(
            "SELECT COUNT(*) FROM sla_timers WHERE resolved=1 AND telegram_pinged=0 AND created_at >= ? AND created_at <= ?",
            (date_from + "T00:00:00", date_to + "T23:59:59")
        ).fetchone()[0]

        tasks_created   = con.execute(
            "SELECT COUNT(*) FROM task_nag_timers WHERE created_at >= ? AND created_at <= ?",
            (date_from + "T00:00:00", date_to + "T23:59:59")
        ).fetchone()[0]
        tasks_completed = con.execute(
            "SELECT COUNT(*) FROM task_nag_timers WHERE acknowledged=1 AND created_at >= ? AND created_at <= ?",
            (date_from + "T00:00:00", date_to + "T23:59:59")
        ).fetchone()[0]

        offenders = con.execute(
            """SELECT tagged_name, COUNT(*) as breaches
               FROM sla_timers
               WHERE telegram_pinged=1 AND created_at >= ? AND created_at <= ?
               GROUP BY tagged_name ORDER BY breaches DESC LIMIT 3""",
            (date_from + "T00:00:00", date_to + "T23:59:59")
        ).fetchall()
        con.close()
    except Exception as db_err:
        log.error("[Scheduler] Weekly report DB aggregation failed: %s", db_err)
        _alert_michael(f"❌ *Weekly Report FAILED* — DB aggregation error:\n`{db_err}`")
        return

    total_sla      = sla_breaches + sla_met
    breach_rate    = round(sla_breaches / total_sla * 100, 1) if total_sla else 0.0
    total_checkins = checkins_ontime + checkins_late + checkins_missing
    offender_text  = ", ".join(f"{r[0]}({r[1]})" for r in offenders) if offenders else "none"

    # ── Stage 2: Sheet recording (continue on failure) ────────────────────────
    sheet_ok = False
    try:
        sheet_ok = sheets_client.log_weekly_kpi(
            week_label=week_label,
            date_from=date_from,
            date_to=date_to,
            checkins_ontime=checkins_ontime,
            checkins_late=checkins_late,
            checkins_missing=checkins_missing,
            eods_submitted=eods_submitted,
            eods_missing=eods_missing,
            sla_breaches=sla_breaches,
            sla_met=sla_met,
            tasks_created=tasks_created,
            tasks_completed=tasks_completed,
            overwrite=overwrite,
        )
    except Exception as sheet_err:
        log.warning("[Scheduler] Weekly KPI sheet write failed: %s", sheet_err)

    sheet_note = "📊 Sheet: ✅ recorded" if sheet_ok else "📊 Sheet: ⚠️ skipped or failed"

    # ── Stage 3: AI report generation ────────────────────────────────────────
    try:
        prompt = (
            f"Write a concise weekly performance summary for a remote agency manager. "
            f"Week {date_from} to {date_to}. Team size: 12.\n"
            f"Check-ins: {checkins_ontime} on-time, {checkins_late} late, {checkins_missing} missing "
            f"(total slots: {total_checkins}).\n"
            f"EOD submissions: {eods_submitted} submitted, {eods_missing} missing.\n"
            f"SLA @mention compliance: {sla_met} met, {sla_breaches} breached ({breach_rate}% breach rate). "
            f"Top breach contributors: {offender_text}.\n"
            f"Tasks: {tasks_created} created, {tasks_completed} completed.\n"
            f"Keep to 4–6 bullet points. Lead with the biggest concern. End with one actionable recommendation."
        )
        ai_text = ai_engine.chat(prompt, task_type="long_context")
    except Exception as ai_err:
        log.error("[Scheduler] Weekly report AI generation failed: %s", ai_err)
        _alert_michael(f"❌ *Weekly Report FAILED* — AI error:\n`{ai_err}`")
        return

    report = (
        f"📊 *Weekly Report — {week_label}* ({date_from} → {date_to})\n\n"
        f"✅ Check-ins: {checkins_ontime}/{total_checkins} on-time | "
        f"{checkins_late} late | {checkins_missing} missing\n"
        f"📋 EOD: {eods_submitted} submitted | {eods_missing} missing\n"
        f"⚡ SLA: {sla_met} met | {sla_breaches} breached ({breach_rate}%)\n"
        f"📌 Tasks: {tasks_created} created | {tasks_completed} completed\n"
        f"{sheet_note}\n\n"
        + (ai_text or "_(AI unavailable — raw data above)_")
    )

    # ── Stage 4: Telegram send (3 retries, exponential backoff) ───────────────
    if not michael or not michael["telegram_chat_id"]:
        log.warning("[Scheduler] Weekly report generated but Michael has no Telegram ID")
        return

    for attempt in range(1, 4):
        try:
            telegram_bot.send_direct(michael["telegram_chat_id"], report)
            log.info("[Scheduler] Weekly report sent to Michael (attempt %d)", attempt)
            break
        except Exception as tg_err:
            wait = 2 ** attempt
            log.warning("[Scheduler] Telegram send attempt %d failed (%s) — retry in %ds", attempt, tg_err, wait)
            if attempt < 3:
                _time.sleep(wait)
            else:
                log.error("[Scheduler] Weekly report Telegram delivery failed after 3 attempts")


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
    # Focus mode check — fires 45-min check-in questions and resumes nags on no-reply.
    scheduler.add_job(
        job_focus_check, "interval", seconds=15, id="focus_check", replace_existing=True
    )
    # Weekly report — every Friday 17:00 Manila time.
    scheduler.add_job(
        job_weekly_report, CronTrigger(
            day_of_week="fri", hour=17, minute=0, timezone=TZ
        ), id="weekly_report", replace_existing=True
    )

    return scheduler
