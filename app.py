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
app = Flask(name)

TOKEN = "8528949259:AAGoesAZiYJ6F99ParjqjYM_isqACiOcko4"
CHAT_ID = "-1003715222874"

RR = 2
ATR_MULT = 1.5
ADX_MIN = 20
noticia_alta_volatilidade = False  # altere manualmente se houver notícia forte

modelo = RandomForestClassifier(n_estimators=300)

historico = {
    "M1": [],
    "M5": []
}

def enviar_mensagem(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "HTML"}
    requests.post(url, json=payload)

def enviar_imagem(caminho):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    files = {"photo": open(caminho, "rb")}
    data = {"chat_id": CHAT_ID}
    requests.post(url, files=files, data=data)

def horario_institucional():
    hora = datetime.now().hour
    return 4 <= hora <= 17  # Londres + NY

def calcular_atr(df, periodo=14):
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift()),
        abs(df['low'] - df['close'].shift())
    ], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()

def calcular_adx(df, periodo=14):
    df['tr'] = calcular_atr(df, periodo)
    df['up_move'] = df['high'] - df['high'].shift()
    df['down_move'] = df['low'].shift() - df['low']

    df['+dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
    df['-dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)

    df['+di'] = 100 * (df['+dm'].rolling(periodo).sum() / df['tr'].rolling(periodo).sum())
    df['-di'] = 100 * (df['-dm'].rolling(periodo).sum() / df['tr'].rolling(periodo).sum())

    dx = (abs(df['+di'] - df['-di']) / (df['+di'] + df['-di'])) * 100
    return dx.rolling(periodo).mean()

def preparar_features(df):
    df['ret'] = df['close'].pct_change()
    df['ma'] = df['close'].rolling(5).mean()
    df['vol'] = df['close'].rolling(5).std()
    df.dropna(inplace=True)
    return df

def treinar(df):
    df = preparar_features(df)
    X = df[['ret','ma','vol']]
    y = np.where(df['close'].shift(-1) > df['close'],1,0)
    modelo.fit(X[:-1], y[:-1])

def prever(df):
    ultima = df.iloc[-1]
    X_pred = [[ultima['ret'], ultima['ma'], ultima['vol']]]
    return modelo.predict(X_pred)[0]

@app.route('/webhook', methods=['POST'])
def webhook():
    global noticia_alta_volatilidade

    data = request.json
    ativo = data.get("ativo")
    tf = data.get("timeframe")

    if ativo not in ["EURUSD","USDJPY"]:
        return {"status":"ignorado"}

    if not horario_institucional():
        return {"status":"fora do horário institucional"}

    if noticia_alta_volatilidade:
        return {"status":"bloqueado por notícia"}

    candle = {
        "open": float(data.get("open")),
        "high": float(data.get("high")),
        "low": float(data.get("low")),
        "close": float(data.get("close"))
    }

    historico[tf].append(candle)

    if len(historico["M1"]) < 50 or len(historico["M5"]) < 50:
        return {"status":"aguardando histórico"}

    df_m1 = pd.DataFrame(historico["M1"][-200:])
    df_m5 = pd.DataFrame(historico["M5"][-200:])

    df_m1['adx'] = calcular_adx(df_m1)
    if df_m1['adx'].iloc[-1] < ADX_MIN:
        return {"status":"mercado lateral"}

    treinar(df_m1)
    direcao_m1 = prever(preparar_features(df_m1))

    treinar(df_m5)
    direcao_m5 = prever(preparar_features(df_m5))

    if direcao_m1 != direcao_m5:
        return {"status":"sem alinhamento"}

    direcao = "COMPRA" if direcao_m1 == 1 else "VENDA"
    emoji = "🟢" if direcao=="COMPRA" else "🔴"

    df_m1['atr'] = calcular_atr(df_m1)
    atr = df_m1['atr'].iloc[-1]
    preco = df_m1['close'].iloc[-1]

    stop = preco - (atr * ATR_MULT) if direcao=="COMPRA" else preco + (atr * ATR_MULT)
    take = preco + (atr * ATR_MULT * RR) if direcao=="COMPRA" else preco - (atr * ATR_MULT * RR)


enviar_mensagem(f"""
<b>⚠️ PREPARAR ENTRADA</b>
📌 {ativo}
⏳ 10 segundos
""")

    time.sleep(10)

    enviar_mensagem(f"""
<b>📊 SINAL INSTITUCIONAL IA</b>

{emoji} {direcao}
💰 {round(preco,5)}
🎯 {round(take,5)}
🛑 {round(stop,5)}
📊 ATR: {round(atr,5)}
📈 ADX: {round(df_m1['adx'].iloc[-1],2)}
""")

    plt.figure(figsize=(10,5))
    plt.plot(df_m1['close'].tail(50))
    plt.axhline(preco, linestyle="--")
    plt.axhline(stop, linestyle="--")
    plt.axhline(take, linestyle="--")
    plt.title(f"{ativo} IA Institucional")
    caminho="grafico_final.png"
    plt.savefig(caminho)
    plt.close()

    enviar_imagem(caminho)

    return {"status":"ok"}

import os

if _name_ == "_main_":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
