import os
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# =============================
# CONFIG (Render Environment)
# =============================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# =============================
# TELEGRAM
# =============================
def enviar_mensagem(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Token ou Chat ID não configurado")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML"
    }

    requests.post(url, json=payload)


# =============================
# WEBHOOK TRADINGVIEW
# =============================
@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.json

    ativo = data.get("ativo", "EURUSD")
    direcao = data.get("direcao", "COMPRA")
    preco = float(data.get("preco", 0))

    mensagem = f"""
📊 <b>SINAL INSTITUCIONAL IA</b>

🔥 Direção: {direcao}
💰 Entrada: {round(preco,5)}

⏳ Entre em 10 segundos
"""

    enviar_mensagem(mensagem)

    return jsonify({"status": "ok"})


# =============================
# HOME (Render precisa disso)
# =============================
@app.route("/")
def home():
    return "Bot Online 🚀"


# =============================
# RUN LOCAL
# =============================
if _name_ == "_main_":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
