import os
import time
import json
import hashlib
import threading
from queue import Queue, Empty
from typing import Any, Dict, Optional, Tuple, List

import requests
from flask import Flask, request, jsonify
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

app = Flask(__name__)

# =========================
# CONFIG (Render ENV VARS)
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

ENABLE_EMOJIS = os.getenv("ENABLE_EMOJIS", "1").strip() == "1"
SIGNATURE = os.getenv("SIGNATURE", "Equipe zmaximusTraders 🚀").strip()

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "20"))
DEFAULT_COUNTDOWN = int(os.getenv("DEFAULT_COUNTDOWN", "12"))

# ===== Filtro de horários =====
TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo").strip()
TRADING_WINDOWS = os.getenv("TRADING_WINDOWS", "09:00-12:00,15:00-17:00").strip()
SEND_IGNORED_NOTICE = os.getenv("SEND_IGNORED_NOTICE", "0").strip() == "1"

# ===== Filtro de notícias =====
NEWS_FILTER_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "0").strip() == "1"
NEWS_SOURCE = os.getenv("NEWS_SOURCE", "faireconomy").strip().lower()  # "faireconomy"
NEWS_IMPACTS = os.getenv("NEWS_IMPACTS", "high").strip().lower()       # "high,medium"
NEWS_LOOKAHEAD_MIN = int(os.getenv("NEWS_LOOKAHEAD_MIN", "30"))        # minutos antes
NEWS_COOLDOWN_AFTER_MIN = int(os.getenv("NEWS_COOLDOWN_AFTER_MIN", "15"))  # depois
NEWS_CACHE_SECONDS = int(os.getenv("NEWS_CACHE_SECONDS", "300"))

# =========================
# STATE (in-memory)
# =========================
_last_signal_hash = None
_last_signal_ts = 0

_TZ = ZoneInfo(TIMEZONE_NAME)

# =========================
# Async queue (anti-timeout)
# =========================
_send_queue: "Queue[Tuple[str, Optional[str]]]" = Queue(maxsize=500)

def _worker():
    while True:
        try:
            text, chat_id = _send_queue.get()
            _telegram_send_message(text, chat_id)
        except Exception:
            pass
        finally:
            try:
                _send_queue.task_done()
            except Exception:
                pass

threading.Thread(target=_worker, daemon=True).start()

def _enqueue_telegram(text: str, chat_id: Optional[str] = None) -> None:
    try:
        _send_queue.put_nowait((text, chat_id))
    except Exception:
        # fila cheia -> melhor descartar do que travar webhook
        pass

# =========================
# Helpers
# =========================
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
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code >= 300:
            return False, f"Telegram erro {r.status_code}: {r.text}"
        return True, "ok"
    except Exception as e:
        return False, str(e)

def _parse_payload() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = (request.data or b"").decode("utf-8", errors="ignore").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}

def _require_secret(payload: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], int]]:
    if not WEBHOOK_SECRET:
        return None
    secret = str(payload.get("secret", "")).strip()
    if secret == WEBHOOK_SECRET:
        return None
    return {"status": "error", "message": "Secret inválido (WEBHOOK_SECRET)."}, 401

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

def _in_trading_window(now: datetime) -> bool:
    cur = now.hour * 60 + now.minute
    for start, end in _WINDOWS:
        if start <= cur <= end:
            return True
    return False

def _is_duplicate(p: Dict[str, Any]) -> bool:
    global _last_signal_hash, _last_signal_ts
    core = {
        "ativo": str(p.get("ativo", p.get("symbol", ""))).upper(),
        "dir": _normalize_direction(str(p.get("acao", p.get("direcao", "")))),
        "tf": str(p.get("timeframe", p.get("tempo", ""))),
        "preco": str(p.get("preco", p.get("price", ""))),
        "strategy": str(p.get("strategy", p.get("estrategia", ""))),
    }
    h = _sha1(json.dumps(core, sort_keys=True))
    now = _now_ts()
    if (now - _last_signal_ts) < COOLDOWN_SECONDS and _last_signal_hash == h:
        return True
    _last_signal_hash = h
    _last_signal_ts = now
    return False

def _symbol_to_currencies(symbol: str) -> List[str]:
    s = (symbol or "").upper().replace("FX:", "").replace("/", "")
    # EURUSD -> ["EUR","USD"]
    if len(s) >= 6:
        return [s[0:3], s[3:6]]
    return []

# =========================
# NEWS FILTER (cache)
# =========================
_news_cache_data: List[Dict[str, Any]] = []
_news_cache_ts: int = 0

def _get_news_events() -> List[Dict[str, Any]]:
    global _news_cache_data, _news_cache_ts
    now = _now_ts()
    if _news_cache_data and (now - _news_cache_ts) < NEWS_CACHE_SECONDS:
        return _news_cache_data

    if NEWS_SOURCE == "faireconomy":
        # Fonte pública bastante usada (ForexFactory calendar mirror)
        url = "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            _news_cache_data = data
            _news_cache_ts = now
            return data

    _news_cache_data = []
    _news_cache_ts = now
    return []

def _impact_ok(impact: str) -> bool:
    allowed = {x.strip() for x in NEWS_IMPACTS.split(",") if x.strip()}
    return (impact or "").strip().lower() in allowed

def _has_high_impact_news(now: datetime, symbol: str) -> Tuple[bool, str]:
    if not NEWS_FILTER_ENABLED:
        return False, ""

    currencies = set(_symbol_to_currencies(symbol))
    if not currencies:
        return False, ""

    events = _get_news_events()
    if not events:
        return False, ""

    # janela de bloqueio
    start = now - timedelta(minutes=NEWS_LOOKAHEAD_MIN)
    end = now + timedelta(minutes=NEWS_COOLDOWN_AFTER_MIN)

    for ev in events:
        try:
            cur = str(ev.get("currency", "")).upper()
            impact = str(ev.get("impact", "")).lower()
            if cur not in currencies:
                continue
            if not _impact_ok(impact):
                continue

            # timestamps do feed costumam vir em unix (segundos)
            ts = ev.get("timestamp")
            if ts is None:
                continue
            ev_dt = datetime.fromtimestamp(int(ts), tz=_TZ)

            if start <= ev_dt <= end:
                title = str(ev.get("title", "Notícia econômica")).strip()
                when = ev_dt.strftime("%d/%m %H:%M")
                return True, f"{cur} • {title} • {when} ({impact})"
        except Exception:
            continue

    return False, ""

def _format_pro_message(p: Dict[str, Any]) -> str:
    ativo = str(p.get("ativo", p.get("symbol", "EURUSD"))).upper()
    direcao = _normalize_direction(str(p.get("acao", p.get("direcao", "CALL"))))
    tf = str(p.get("timeframe", p.get("tempo", "1"))).strip()
    preco = p.get("preco", p.get("price", ""))
    strategy = str(p.get("strategy", p.get("estrategia", "Zmaximus PRO"))).strip()
    countdown = int(p.get("countdown", DEFAULT_COUNTDOWN))
    broker = str(p.get("broker", p.get("corretora", "IQ Option / Exnova / Quotex"))).strip()

    ts = datetime.now(_TZ).strftime("%d/%m %H:%M:%S")

    titulo = f"{_emoji('📌')} <b>SINAL PRO (Renko + Continuidade)</b>"
    linha1 = f"{_emoji('💱')} <b>Ativo:</b> {ativo}  |  <b>TF:</b> {tf}m"
    linha2 = f"{_emoji('🎯')} <b>Ação:</b> <b>{direcao}</b>"
    linha3 = f"{_emoji('💰')} <b>Entrada:</b> {preco}"
    linha4 = f"{_emoji('🧠')} <b>Estratégia:</b> {strategy}"
    linha5 = f"{_emoji('🏦')} <b>Corretora:</b> {broker}"
    linha6 = f"{_emoji('⏳')} <b>Entre em:</b> {countdown}s"
    rodape = f"\n\n{_emoji('🕒')} <i>{ts}</i>\n<b>{SIGNATURE}</b>"

    return (
        f"{titulo}\n\n"
        f"{linha1}\n{linha2}\n{linha3}\n{linha4}\n{linha5}\n\n"
        f"{linha6}{rodape}"
    )

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

    sec = _require_secret(payload)
    if sec:
        return jsonify(sec[0]), sec[1]

    now = datetime.now(_TZ)

    # 1) filtro de horário
    if not _in_trading_window(now):
        if SEND_IGNORED_NOTICE:
            _enqueue_telegram(
                f"{_emoji('⛔️')} <b>SINAL IGNORADO</b>\n"
                f"Motivo: fora do horário\n"
                f"🕒 <i>{now.strftime('%d/%m %H:%M:%S')}</i>\n"
                f"⏱️ Janelas: <b>{TRADING_WINDOWS}</b>\n\n"
                f"<b>{SIGNATURE}</b>"
            )
        return jsonify({"status": "ignored", "message": "blocked_by_time"}), 200

    # 2) filtro de notícias
    symbol = str(payload.get("ativo", payload.get("symbol", ""))).upper()
    blocked, reason = _has_high_impact_news(now, symbol)
    if blocked:
        if SEND_IGNORED_NOTICE:
            _enqueue_telegram(
                f"{_emoji('📰')} <b>SINAL BLOQUEADO (NOTÍCIA)</b>\n"
                f"Ativo: <b>{symbol}</b>\n"
                f"Evento: <b>{reason}</b>\n\n"
                f"<b>{SIGNATURE}</b>"
            )
        return jsonify({"status": "ignored", "message": "blocked_by_news", "reason": reason}), 200

    # 3) anti-spam
    if _is_duplicate(payload):
        return jsonify({"status": "ignored", "message": "duplicate"}), 200

    # 4) monta mensagem
    msg = _format_pro_message(payload)

    # 5) IMPORTANTÍSSIMO: responde logo pro TradingView
    _enqueue_telegram(msg)
    return jsonify({"status": "ok"}), 200




