import telebot
import subprocess
import os
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MarketingBot] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR  = "/home/mikenam/projects/MarketingAgency"
WIKI_DIR  = os.path.join(BASE_DIR, "wiki")
NPX_PATH  = "/home/mikenam/.nvm/versions/node/v20.20.2/bin/npx"
TOKEN     = "8412230853:AAGJJxFtp7wc3EpbORlBgEj1HvEP-eYqLgY"
MY_CHAT_ID = "1460264431"

# Subprocess env with NVM node_modules on PATH so npx resolves correctly
_ENV = {**os.environ, "PATH": f"/home/mikenam/.nvm/versions/node/v20.20.2/bin:{os.environ.get('PATH', '')}"}

bot = telebot.TeleBot(TOKEN, parse_mode=None)


@bot.message_handler(func=lambda m: str(m.chat.id) == MY_CHAT_ID)
def handle_command(message):
    user_query = message.text.strip()
    if not user_query:
        return

    log.info("Received: %s", user_query[:120])
    bot.reply_to(message, "⏳ Processing...")

    instruction = (
        f"Working directory: {BASE_DIR}\n"
        f"Context: read '{WIKI_DIR}' if it exists before answering.\n"
        f"Timezone: PST (America/Los_Angeles).\n"
        f"Request: {user_query}"
    )

    try:
        result = subprocess.run(
            [NPX_PATH, "@anthropic-ai/claude-code", "-p", instruction],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=BASE_DIR,
            env=_ENV,
        )
        output = (result.stdout.strip() or result.stderr.strip())[:4000]
        reply = output if output else "⚠️ Claude Code returned no output."
    except subprocess.TimeoutExpired:
        reply = "⏱️ Timed out after 10 minutes."
    except Exception as e:
        reply = f"❌ Error: {e}"
        log.error("Subprocess error: %s", e)

    bot.send_message(MY_CHAT_ID, reply)
    log.info("Replied (%d chars)", len(reply))


log.info("MarketingBot starting — chat_id=%s", MY_CHAT_ID)
while True:
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=25)
    except Exception as e:
        log.error("Polling crashed: %s — restarting in 5s", e)
        time.sleep(5)
