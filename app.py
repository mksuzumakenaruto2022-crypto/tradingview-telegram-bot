import os
import time
import json
import hashlib
from typing import Any, Dict, Optional, Tuple, List

import requests
from flask import Flask, request, jsonify

from zoneinfo import ZoneInfo
from datetime import datetime

app = Flask(_name_)

# =========================
# CONFIG (Render ENV VARS)
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()  # senha forte

ENABLE_EMOJIS = os.getenv("ENABLE_EMOJIS", "1").strip() == "1"
LANG_STYLE = os.getenv("LANG_STYLE", "pro").strip().lower()
SIGNATURE = os.getenv("SIGNATURE", "Equipe zmaximusTraders 🚀").strip()

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "20"))
DEFAULT_COUNTDOWN = int(os.getenv("DEFAULT_COUNTDOWN", "12"))

# ===== Filtro de horários =====
TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo").strip()
TRADING_WINDOWS = os.getenv("TRADING_WINDOWS", "09:00-12:00,15:00-17:00").strip()
SEND_IGNORED_NOTICE = os.getenv("SEND_IGNORED_NOTICE", "0").strip() == "1"

# ===== Filtro de dias avançado =====
ALLOW_WEEKENDS = os.getenv("ALLOW_WEEKENDS", "0").strip() == "1"
BLOCK_FRIDAY_AFTER = os.getenv("BLOCK_FRIDAY_AFTER", "17:00").strip()  # HH:MM
ALLOW_SUNDAY_AFTER = os.getenv("ALLOW_SUNDAY_AFTER", "").strip()       # HH:MM (opcional)

# ===== Admin (comandos Telegram) =====
ADMIN_TELEGRAM_IDS = os.getenv("ADMIN_TELEGRAM_IDS", "").strip()  # "123,456"
START_PAUSED = os.getenv("START_PAUSED", "0").strip() == "1"

# =========================
# STATE (in-memory)
# =========================
_last_signal_hash = None
_last_signal_ts = 0
PAUSED = START_PAUSED

# =========================
# Helpers
# =========================
_TZ = ZoneInfo(TIMEZONE_NAME)

def _now_ts() -> int:
    return int(time.time())

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _emoji(on: str, off: str = "") -> str:
    return on if ENABLE_EMOJIS else off

def _normalize_direction(d: str) -> str:
    d = (d or "").strip().upper()
    if d in ["CALL", "BUY", "COMPRA", "UP"]:
        return "CALL"
    if d in ["PUT", "SELL", "VENDA", "DOWN"]:
        return "PUT"
    return d or "CALL"

def _telegram_send_message(text: str, chat_id: Optional[str] = None) -> Tuple[bool, str]:
    if not TELEGRAM_BOT_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN não configurado."
    if not (chat_id or TELEGRAM_CHAT_ID):
        return False, "TELEGRAM_CHAT_ID não configurado."

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code >= 300:
            return False, f"Telegram erro {r.status_code}: {r.text}"
        return True, "ok"
    except Exception as e:
        return False, str(e)

def _require_secret_from_tradingview(payload: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], int]]:
    if not WEBHOOK_SECRET:
        return None
    secret = str(payload.get("secret", "")).strip()
    if secret == WEBHOOK_SECRET:
        return None
    # também aceita header (caso você use futuramente)
    header_secret = request.headers.get("X-WEBHOOK-SECRET", "").strip()
    if header_secret == WEBHOOK_SECRET:
        return None
    return {"status": "error", "message": "Secret inválido (WEBHOOK_SECRET)."}, 401

def _parse_payload() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = (request.data or b"").decode("utf-8", errors="ignore").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start:end+1])
                except Exception:
                    pass
    return {}

def _parse_windows(s: str) -> List[Tuple[int, int]]:
    windows = []
    parts = [p.strip() for p in s.split(",") if p.strip()]
    for p in parts:
        if "-" not in p:
            continue
        a, b = [x.strip() for x in p.split("-", 1)]
        sh, sm = [int(x) for x in a.split(":")]
        eh, em = [int(x) for x in b.split(":")]
        windows.append((sh * 60 + sm, eh * 60 + em))
    return windows

_WINDOWS = _parse_windows(TRADING_WINDOWS)

def _hhmm_to_minutes(hhmm: str) -> Optional[int]:
    if not hhmm:
        return None
    try:
        h, m = [int(x) for x in hhmm.split(":")]
        return h * 60 + m
    except Exception:
        return None

_FRIDAY_CUTOFF = _hhmm_to_minutes(BLOCK_FRIDAY_AFTER)
_SUNDAY_ALLOW = _hhmm_to_minutes(ALLOW_SUNDAY_AFTER)

def _in_trading_window(now: datetime) -> bool:
    # 1) pausa manual
    if PAUSED:
        return False

    wd = now.weekday()  # 0 seg ... 4 sex ... 5 sab ... 6 dom
    cur = now.hour * 60 + now.minute

    # 2) fim de semana
    if not ALLOW_WEEKENDS:
        # sábado bloqueado sempre
        if wd == 5:
            return False
        # domingo: pode liberar depois de um horário
        if wd == 6:
            if _SUNDAY_ALLOW is None:
                return False
            if cur < _SUNDAY_ALLOW:
                return False

    # 3) sexta após horário de corte
    if wd == 4 and _FRIDAY_CUTOFF is not None:
        if cur >= _FRIDAY_CUTOFF:
            return False

    # 4) janelas do dia
    for start, end in _WINDOWS:
        if start <= cur <= end:
            return True
    return False

def _format_pro_message(p: Dict[str, Any]) -> str:
    ativo = str(p.get("ativo", p.get("symbol", "EURUSD"))).upper()
    direcao = _normalize_direction(str(p.get("acao", p.get("direcao", "CALL"))))
    tf = str(p.get("timeframe", p.get("tempo", "1"))).strip()
    preco = p.get("preco", p.get("price", 0))
    strategy = str(p.get("strategy", p.get("estrategia", "Zmaximus PRO"))).strip()
    countdown = int(p.get("countdown", DEFAULT_COUNTDOWN))
    broker = str(p.get("broker", p.get("corretora", "IQ Option / Exnova / Quotex"))).strip()

    conf = p.get("conf", p.get("confidence"))
    conf_txt = ""
    if conf is not None:
        try:
            conf_txt = f"{int(float(conf))}%"
        except Exception:
            conf_txt = str(conf)

    conf_list = p.get("confluencias", p.get("confluences", []))
    if isinstance(conf_list, str):
        conf_items = [x.strip() for x in conf_list.split("|") if x.strip()]
    elif isinstance(conf_list, list):
        conf_items = [str(x).strip() for x in conf_list if str(x).strip()]
    else:
        conf_items = []

    ts = datetime.now(_TZ).strftime("%d/%m %H:%M:%S")

    titulo = f"{_emoji('📌')} <b>SINAL PRO (Renko + Continuidade)</b>"
    linha1 = f"{_emoji('💱')} <b>Ativo:</b> {ativo}  |  <b>TF:</b> {tf}m"
    linha2 = f"{_emoji('🎯')} <b>Ação:</b> <b>{direcao}</b>"
    linha3 = f"{_emoji('💰')} <b>Entrada:</b> {preco}"
    linha4 = f"{_emoji('🧠')} <b>Estratégia:</b> {strategy}"
    linha5 = f"{_emoji('🏦')} <b>Corretora:</b> {broker}"
    linha6 = f"{_emoji('⏳')} <b>Entre em:</b> {countdown}s"

    bloco_conf = f"\n{_emoji('📈')} <b>Confiança:</b> {conf_txt}" if conf_txt else ""

    bloco_check = ""
    if conf_items:
        linhas = "\n".join([f"{_emoji('✅','-')} {item}" for item in conf_items])
        bloco_check = f"\n\n{_emoji('🧩')} <b>Confluências:</b>\n{linhas}"

    rodape = f"\n\n{_emoji('🕒')} <i>{ts}</i>\n<b>{SIGNATURE}</b>"

    return (
        f"{titulo}\n\n"
        f"{linha1}\n{linha2}\n{linha3}\n{linha4}\n{linha5}"
        f"{bloco_conf}{bloco_check}\n\n{linha6}{rodape}"
    )

def _is_duplicate(p: Dict[str, Any]) -> bool:
    global _last_signal_hash, _last_signal_ts
    core = {
        "ativo": str(p.get("ativo", p.get("symbol", ""))).upper(),
        "dir": _normalize_direction(str(p.get("acao", p.get("direcao", "")))),
        "tf": str(p.get("timeframe", p.get("tempo", ""))),
        "preco": str(p.get("preco", p.get("price", ""))),
        "strategy": str(p.get("strategy", p.get("estrategia", ""))),
        "conf": str(p.get("conf", "")),
    }
    h = _sha1(json.dumps(core, sort_keys=True))
    now = _now_ts()

    if (now - _last_signal_ts) < COOLDOWN_SECONDS and _last_signal_hash == h:
        return True
    if _last_signal_hash == h and (now - _last_signal_ts) < 120:
        return True

    _last_signal_hash = h
    _last_signal_ts = now
    return False

def _admin_ids() -> List[int]:
    ids = []
    for part in ADMIN_TELEGRAM_IDS.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            pass
    return ids

def _is_admin(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    admins = _admin_ids()
    return user_id in admins if admins else False


# =========================
# ROUTES
# =========================
@app.get("/")
def home():
    return "Bot Online ✅"

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    payload = _parse_payload()
    if not payload:
        return jsonify({"status": "error", "message": "Payload vazio/ inválido."}), 400

    sec = _require_secret_from_tradingview(payload)
    if sec:
        return jsonify(sec[0]), sec[1]

    now = datetime.now(_TZ)
    if not _in_trading_window(now):
        if SEND_IGNORED_NOTICE:
            msg = (
                f"{_emoji('⛔')} <b>SINAL IGNORADO</b>\n"
                f"• Motivo: fora do horário / pausado\n"
                f"🕒 <i>{now.strftime('%d/%m %H:%M:%S')}</i>\n"
                f"⏱ Janelas: <b>{TRADING_WINDOWS}</b>\n"
                f"📅 Regra: <b>sex após {BLOCK_FRIDAY_AFTER} bloqueia</b>\n"
                f"{'📅 Domingo libera após ' + ALLOW_SUNDAY_AFTER if ALLOW_SUNDAY_AFTER else '📅 Domingo bloqueado'}\n\n"
                f"<b>{SIGNATURE}</b>"
            )
            _telegram_send_message(msg)
        return jsonify({"status": "ignored", "message": "blocked_by_time_or_pause"}), 200

    if _is_duplicate(payload):
        return jsonify({"status": "ignored", "message": "duplicate (anti-spam)"}), 200

    msg = _format_pro_message(payload)
    ok, info = _telegram_send_message(msg)
    if not ok:
        return jsonify({"status": "error", "message": info}), 500
    return jsonify({"status": "ok"}), 200

@app.post("/telegram")
def telegram_webhook():
    """
    Webhook do Telegram para comandos:
    /pause, /resume, /status
    """
    global PAUSED

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}

    chat_id = chat.get("id")
    user_id = from_user.get("id")

    if not text:
        return jsonify({"ok": True})

    # somente admin
    if not _is_admin(user_id):
        # responde no privado do usuário (se possível)
        if chat_id:
            _telegram_send_message("⛔ Você não tem permissão para esse comando.", str(chat_id))
        return jsonify({"ok": True})

    cmd = text.split()[0].lower()

    if cmd == "/pause":
        PAUSED = True
        _telegram_send_message("⏸️ <b>Sinais PAUSADOS</b>\nUse /resume para voltar.", str(chat_id))
    elif cmd == "/resume":
        PAUSED = False
        _telegram_send_message("▶️ <b>Sinais ATIVADOS</b>\nBot voltou a operar.", str(chat_id))
    elif cmd == "/status":
        now = datetime.now(_TZ)
        status = "PAUSADO ⏸️" if PAUSED else "ATIVO ▶️"
        allowed = "SIM ✅" if _in_trading_window(now) else "NÃO ⛔"
        msg = (
            f"📡 <b>Status do Bot</b>\n\n"
            f"• Estado: <b>{status}</b>\n"
            f"• Agora: <b>{now.strftime('%d/%m %H:%M:%S')}</b>\n"
            f"• Dentro da janela: <b>{allowed}</b>\n\n"
            f"• Janelas: <b>{TRADING_WINDOWS}</b>\n"
            f"• Sex após: <b>{BLOCK_FRIDAY_AFTER}</b> (bloqueia)\n"
            f"• Domingo: <b>{('libera após ' + ALLOW_SUNDAY_AFTER) if ALLOW_SUNDAY_AFTER else 'bloqueado'}</b>\n\n"
            f"<b>{SIGNATURE}</b>"
        )
        _telegram_send_message(msg, str(chat_id))
    else:
        _telegram_send_message("Comandos: /pause | /resume | /status", str(chat_id))

    return jsonify({"ok": True})

if _name_ == "_main_":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


