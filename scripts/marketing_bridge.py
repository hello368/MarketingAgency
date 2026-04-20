import telebot
import subprocess
import shlex
import os

# 1. 절대 경로 자동 설정 (파일 위치 기준)
BASE_DIR = "/home/mikenam/projects/MarketingAgency"
WIKI_DIR = os.path.join(BASE_DIR, "wiki")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

TOKEN = '8412230853:AAGJJxFtp7wc3EpbORlBgEj1HvEP-eYqLgY'
MY_CHAT_ID = '1460264431'

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(func=lambda message: str(message.chat.id) == MY_CHAT_ID)
def handle_command(message):
    user_query = message.text
    bot.reply_to(message, "🛠️ 시스템 최적화 모드로 분석을 시작합니다...")

    try:
        # Claude에게 절대 경로를 직접 주입하여 혼선을 방지합니다.
        instruction = (
            f"You are working in the directory: {BASE_DIR}\n"
            f"1. Mandatory: Read context from '{WIKI_DIR}' before answering.\n"
            f"2. Permission: You have full rights to write to '{WIKI_DIR}'.\n"
            f"3. Rule: Update summary in '{WIKI_DIR}/summary.md' with new findings.\n"
            f"User Request: {user_query}"
        )
        
        # 명령어 실행 시 작업 디렉토리를 BASE_DIR로 고정 (cwd 사용)
        safe_cmd = f"npx @anthropic-ai/claude-code -c {shlex.quote(instruction)}"
        
        result = subprocess.run(
            safe_cmd, shell=True, capture_output=True, text=True, timeout=600,
            cwd=BASE_DIR  # 이 한 줄이 경로 에러를 99% 잡아줍니다.
        )
        
        output = result.stdout.strip()
        
        if output:
            bot.send_message(MY_CHAT_ID, f"✅ **분석 결과:**\n\n{output[:4000]}")
            
            # 파일 존재 여부 실시간 체크 및 전송
            mmd_file = os.path.join(WIKI_DIR, "knowledge_graph.mmd")
            if os.path.exists(mmd_file):
                with open(mmd_file, "rb") as f:
                    bot.send_document(MY_CHAT_ID, f, caption="📊 최신 지식 그래프입니다.")

    except Exception as e:
        bot.send_message(MY_CHAT_ID, f"❌ 오류 발생: {str(e)}")

print(f"🚀 최적화된 비서가 가동 중입니다! (경로: {BASE_DIR})")
bot.polling()