import os
import json
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(_name_)

# ==========================
# CONFIG (Render Environment)
# ==========================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # ex: -1002500963544
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")  # igual ao secret do Pine (opcional, mas recomendado)

# Telegram settings
TELEGRAM_PARSE_MODE = os.environ.get("TELEGRAM_PARSE_MODE", "HTML")  # "HTML" ou "Markdown"
DISABLE_PREVIEW = True


def enviar_mensagem(texto: str):
    """Envia mensagem pro Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Token ou Chat ID não configurado (Render env vars).")
        return {"ok": False, "error": "missing telegram env vars"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "disable_web_page_preview": DISABLE_PREVIEW,
        "parse_mode": TELEGRAM_PARSE_MODE
    }

    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def _to_int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def _safe_str(v, default=""):
    return default if v is None else str(v)


def montar_mensagem_fallback(data: dict) -> str:
    """
    Caso NÃO venha telegram_text do Pine, monta uma mensagem padrão.
    Aceita tanto:
      - "acao" (CALL/PUT)
      - ou "direcao" (COMPRA/VENDA)
    """

    ativo = _safe_str(data.get("ativo", "EURUSD"))
    timeframe = _safe_str(data.get("timeframe", "1m"))
    preco = _safe_str(data.get("preco", data.get("price", "")))
    expiracao = _to_int(data.get("expiracao_seg", data.get("expiracao", 60)), 60)
    delay = _to_int(data.get("delay_entrada_seg", 12), 12)
    assinatura = _safe_str(data.get("assinatura", "Equipe zmaximusTraders 🚀"))

    # ação pode vir como CALL/PUT
    acao = _safe_str(data.get("acao", "")).upper().strip()

    # ou pode vir como direcao COMPRA/VENDA
    direcao = _safe_str(data.get("direcao", "")).upper().strip()

    if not acao:
        if "COMPRA" in direcao:
            acao = "CALL"
        elif "VENDA" in direcao:
            acao = "PUT"
        else:
            acao = "SINAL"

    acao_txt = "✅ CALL (COMPRA)" if acao == "CALL" else ("🔻 PUT (VENDA)" if acao == "PUT" else f"📣 {acao}")

    candle_time = _safe_str(data.get("candle_time", ""))

    # Se não veio candle_time, usa hora do servidor
    if not candle_time:
        candle_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # Hora sugerida (aprox): agora + delay
    entrada_time = (datetime.now() + timedelta(seconds=delay)).strftime("%H:%M:%S")

    rsi = _safe_str(data.get("rsi", ""))
    ema = _safe_str(data.get("ema", ""))

    extras = []
    if rsi:
        extras.append(f"RSI: {rsi}")
    if ema:
        extras.append(f"EMA: {ema}")

    extras_txt = f"📊 " + " | ".join(extras) + "\n" if extras else ""

    msg = (
        f"<b>{acao_txt}</b>\n"
        f"📌 <b>Ativo:</b> {ativo}\n"
        f"⏱ <b>Timeframe:</b> {timeframe}\n"
        f"💰 <b>Preço:</b> {preco}\n"
        f"⏳ <b>Entre em:</b> {delay}s (≈ {entrada_time})\n"
        f"🎯 <b>Expiração:</b> {expiracao}s\n"
        f"🕒 <b>Candle:</b> {candle_time}\n"
        f"{extras_txt}\n"
        f"{assinatura}"
    )

    return msg


# ==========================
# WEBHOOK TRADINGVIEW
# ==========================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    print("📩 Webhook recebido:", json.dumps(data, ensure_ascii=False)[:600])

    # (RECOMENDADO) validar secret
    if WEBHOOK_SECRET:
        secret = _safe_str(data.get("secret", ""))
        if secret != WEBHOOK_SECRET:
            print("❌ Secret inválido.")
            return jsonify({"ok": False, "error": "invalid secret"}), 403

    # Se o Pine enviar telegram_text, usa ele
    telegram_text = data.get("telegram_text")

    if telegram_text and isinstance(telegram_text, str) and telegram_text.strip():
        texto = telegram_text.strip()
    else:
        texto = montar_mensagem_fallback(data)

    try:
        resp = enviar_mensagem(texto)
        print("✅ Telegram enviado:", resp)
        return jsonify({"status": "ok", "telegram": "sent"})
    except Exception as e:
        print("❌ Erro ao enviar Telegram:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


# ==========================
# HOME (Render precisa disso)
# ==========================
@app.route("/")
def home():
    return "Bot Online 🚀"


if _name_ == "_main_":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
