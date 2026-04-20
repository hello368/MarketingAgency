"""
Telegram Bot — two responsibilities:
1. /register [Name] command → maps Telegram chat_id to team member
2. send_alert(name, message) → pushes urgent notifications
"""
import os
import time
import logging
import threading
from pathlib import Path
from dotenv import load_dotenv
import telebot
from telebot.types import Message
import db

load_dotenv(Path(__file__).parent / ".env")
log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")


# ─────────────────────────────────────────
# Registration Flow
# ─────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    bot.reply_to(
        message,
        "👋 *Agency Tracker Bot*\n\n"
        "To link your Telegram to the tracking system, send:\n"
        "`/register YourFirstName`\n\n"
        "Use the exact first name you use in Google Chat.\n"
        "Example: `/register Tiffany`"
    )


@bot.message_handler(commands=["register"])
def handle_register(message: Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            "❌ Please include your name.\nExample: `/register Tiffany`"
        )
        return

    name = parts[1].strip()
    telegram_id = message.chat.id
    success = db.register_telegram(name, telegram_id)

    if success:
        member = db.get_member_by_telegram_id(telegram_id)
        confirmed_name = member["name"] if member else name
        bot.reply_to(
            message,
            f"✅ *{confirmed_name} registered successfully!* "
            f"You will receive 15-min SLA alerts here."
        )
        log.info("[Telegram] Registered: %s → chat_id=%s", confirmed_name, telegram_id)
    else:
        bot.reply_to(
            message,
            f"❌ *{name}* is not in the team list.\n\n"
            f"Use your exact first name from Google Chat.\n"
            f"Contact Kaye or Anna for help."
        )
        log.warning("[Telegram] Registration failed for name: %s", name)


@bot.message_handler(commands=["status"])
def handle_status(message: Message):
    """Let a registered user check their own registration status."""
    telegram_id = message.chat.id
    member = db.get_member_by_telegram_id(telegram_id)
    if member:
        bot.reply_to(
            message,
            f"✅ You are registered as *{member['name']}* ({member['group_name']}).\n"
            f"Google Chat ID: `{member['google_chat_id'] or 'Not yet linked'}`"
        )
    else:
        bot.reply_to(
            message,
            "❌ You are not registered yet. Send `/register YourName` to link your account."
        )


# ─────────────────────────────────────────
# Alert Sender (called by SLA engine)
# ─────────────────────────────────────────

def send_alert(member_name: str, alert_text: str) -> bool:
    """
    Send a Telegram message to a team member by name.
    Returns True if the message was sent, False if no Telegram ID found.
    """
    member = db.get_member_by_name(member_name)
    if not member or not member["telegram_chat_id"]:
        log.warning("[Telegram] No Telegram ID for %s — cannot send alert", member_name)
        return False
    try:
        bot.send_message(member["telegram_chat_id"], alert_text, parse_mode="Markdown")
        log.info("[Telegram] Alert sent to %s", member_name)
        return True
    except Exception as e:
        log.error("[Telegram] send_alert failed for %s: %s", member_name, e)
        return False


def send_direct(telegram_chat_id: int, text: str):
    """Send a message directly to a Telegram chat_id."""
    try:
        bot.send_message(telegram_chat_id, text, parse_mode="Markdown")
    except Exception as e:
        log.error("[Telegram] send_direct failed: %s", e)


# ─────────────────────────────────────────
# Bot Polling (runs in background thread)
# ─────────────────────────────────────────

def start_polling():
    """
    Start Telegram bot in a daemon thread with an automatic retry loop.
    infinity_polling() can crash silently on 409 conflicts (two getUpdates
    sessions racing during --reload restarts). The retry loop catches every
    exception, logs it visibly, and reconnects after 5 seconds.
    """
    def _poll():
        while True:
            try:
                # Always clear webhook first — a registered webhook causes a
                # 409 Conflict that kills polling immediately and silently.
                bot.remove_webhook()
                log.info("[Telegram] ✅ Webhook cleared — starting long-poll loop")

                me = bot.get_me()
                log.info("[Telegram] Bot identity confirmed: @%s (id=%s)", me.username, me.id)

                bot.infinity_polling(
                    timeout=20,
                    long_polling_timeout=15,
                    logger_level=logging.INFO,
                )
                # infinity_polling() only exits on fatal error — log and retry
                log.warning("[Telegram] infinity_polling() exited unexpectedly — retrying in 5s")

            except Exception as e:
                log.error("[Telegram] ❌ Polling crashed: %s", e, exc_info=True)
                log.info("[Telegram] Retrying in 5s...")

            time.sleep(5)

    t = threading.Thread(target=_poll, daemon=True, name="telegram-bot")
    t.start()
    log.info("[Telegram] Polling thread launched")
