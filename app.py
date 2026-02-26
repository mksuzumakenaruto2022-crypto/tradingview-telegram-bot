import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from flask import Flask, request
import requests
import time
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from datetime import datetime
import os

app = Flask(_name_)

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ATR_MULT = 1.5
RR = 2

def enviar_mensagem(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

@app.route("/")
def home():
    return "Bot online 🚀"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    ativo = data.get("ativo", "EURUSD")
    direcao = data.get("direcao", "COMPRA")
    preco = float(data.get("preco", 1.0000))

    atr = 0.0010
    stop = preco - (atr * ATR_MULT) if direcao == "COMPRA" else preco + (atr * ATR_MULT)
    take = preco + (atr * ATR_MULT * RR) if direcao == "COMPRA" else preco - (atr * ATR_MULT * RR)

    enviar_mensagem(f"""
<b>⚠ PREPARAR ENTRADA</b>

📌 {ativo}
⏳ 10 segundos
""")

    time.sleep(10)

    enviar_mensagem(f"""
<b>📊 SINAL INSTITUCIONAL IA</b>

🔥 {direcao}
💰 Entrada: {round(preco,5)}
🎯 Take: {round(take,5)}
🛑 Stop: {round(stop,5)}
""")

    return {"status": "ok"}

