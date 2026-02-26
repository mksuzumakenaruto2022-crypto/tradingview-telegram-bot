import os
import time
import requests

# Matplotlib no Render (sem interface gráfica)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from flask import Flask, request, jsonify

app = Flask(_name_)

# ====== CONFIG (Render -> Environment Variables) ======
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Opcional (padrão 10)
COUNTDOWN_SECONDS = int(os.environ.get("COUNTDOWN_SECONDS", "10"))

# ======================================================


def _telegram_api(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def enviar_mensagem(texto_html: str) -> None:
    """Envia mensagem HTML para o Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados nas variáveis de ambiente.")

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(_telegram_api("sendMessage"), json=payload, timeout=20)
    r.raise_for_status()


def enviar_imagem(caminho: str, legenda_html: str = "") -> None:
    """Envia imagem para o Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados nas variáveis de ambiente.")

    with open(caminho, "rb") as f:
        files = {"photo": f}
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": legenda_html,
            "parse_mode": "HTML",
        }
        r = requests.post(_telegram_api("sendPhoto"), data=data, files=files, timeout=60)
        r.raise_for_status()


def gerar_grafico_simples(closes, titulo: str, caminho: str) -> None:
    """Gera um gráfico simples com os últimos closes."""
    plt.figure(figsize=(10, 4))
    plt.plot(closes)
    plt.title(titulo)
    plt.tight_layout()
    plt.savefig(caminho, dpi=150)
    plt.close()


def montar_texto_sinal(data: dict) -> str:
    ativo = str(data.get("ticker") or data.get("symbol") or data.get("ativo") or "ATIVO").upper()
    direcao = str(data.get("direction") or data.get("direcao") or data.get("side") or "").upper()

    # aceita BUY/SELL também
    if direcao in ("BUY", "CALL", "COMPRA"):
        direcao_fmt = "COMPRA ✅"
        emoji = "🟢"
    elif direcao in ("SELL", "PUT", "VENDA"):
        direcao_fmt = "VENDA ❌"
        emoji = "🔴"
    else:
        direcao_fmt = direcao if direcao else "N/D"
        emoji = "⚪"

    preco = data.get("price") or data.get("preco")
    take = data.get("take")
    stop = data.get("stop")

    def fmt(x):
        try:
            return f"{float(x):.5f}"
        except Exception:
            return str(x) if x is not None else "-"

    texto = (
        f"<b>📊 SINAL INSTITUCIONAL IA</b>\n\n"
        f"🎯 <b>Ativo:</b> {ativo}\n"
        f"{emoji} <b>Direção:</b> {direcao_fmt}\n"
        f"⏱️ <b>Entrada em:</b> {COUNTDOWN_SECONDS} segundos\n\n"
        f"💰 <b>Entrada:</b> {fmt(preco)}\n"
        f"🎯 <b>Take:</b> {fmt(take)}\n"
        f"🛑 <b>Stop:</b> {fmt(stop)}\n\n"
        f"<i>Equipe zmaximusTraders 🚀</i>"
    )
    return texto


@app.route("/", methods=["GET"])
def home():
    return "Bot online 🚀", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView alert -> POST JSON
    Exemplo de JSON:
    {
      "ticker":"EURUSD",
      "direction":"BUY",
      "price":1.23456,
      "take":1.24000,
      "stop":1.23000,
      "closes":[1.23,1.231, ...]   (opcional)
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "JSON inválido"}), 400

        texto = montar_texto_sinal(data)

        # 1) mensagem "preparar entrada"
        enviar_mensagem(
            f"<b>⚠️ PREPARAR ENTRADA</b>\n"
            f"⭐ {str(data.get('ticker') or data.get('symbol') or data.get('ativo') or 'ATIVO').upper()}\n"
            f"⏳ {COUNTDOWN_SECONDS} segundos"
        )

        # 2) contagem (servidor espera)
        time.sleep(COUNTDOWN_SECONDS)

        # 3) envia sinal final
        enviar_mensagem(texto)

        # 4) gráfico opcional (se vier closes)
        closes = data.get("closes")
        if isinstance(closes, list) and len(closes) >= 10:
            caminho = "grafico_final.png"
            gerar_grafico_simples(closes[-50:], f"{data.get('ticker','ATIVO')} - Gráfico (últimos closes)", caminho)
            enviar_imagem(caminho, legenda_html="📈 Gráfico do sinal")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        # Mostra erro no log do Render
        return jsonify({"status": "error", "message": str(e)}), 500


if _name_ == "_main_":
    # Local: python app.py
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
