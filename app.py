from flask import Flask, request
import requests
import os

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("CHAT_ID")


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    requests.post(url, json=payload)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    
    symbol = data.get("symbol", "N/A")
    direction = data.get("direction", "N/A")
    timeframe = data.get("timeframe", "N/A")

    message = f"""
🚨 NOVO SINAL 🚨

📊 Ativo: {symbol}
⏰ Timeframe: {timeframe}
📈 Direção: {direction}

Boa sorte 🍀
"""

    send_telegram_message(message)
    return {"status": "ok"}

@app.route("/")
def home():
    return "Bot Online 🚀"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
