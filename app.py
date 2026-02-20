from flask import Flask, request
import requests
import os

app = Flask(__name__)

# Variáveis de ambiente (configuradas na Render)
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("CHAT_ID")

# Função para enviar mensagem ao Telegram
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    requests.post(url, json=payload)

# Rota principal (teste online)
@app.route("/")
def home():
    return "Bot Online 🚀"

# Webhook que recebe alerta do TradingView
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    symbol = data.get("symbol", "N/A")
    direction = data.get("direction", "N/A")
    timeframe = data.get("timeframe", "N/A")

    message = f"""
📊 NOVO SINAL

📌 Ativo: {symbol}
⏱ Timeframe: {timeframe}
📈 Direção: {direction}

Boa sorte 🚀
"""

    send_telegram_message(message)

    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
