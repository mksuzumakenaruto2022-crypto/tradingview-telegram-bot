import os
import time
import json
import hashlib
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime
from queue import Queue
import threading

import requests
from flask import Flask, request, jsonify

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

app = Flask(__name__)

# =========================
# ENV / CONFIG
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo").strip()
TRADING_WINDOWS = os.getenv("TRADING_WINDOWS", "09:00-12:00,15:00-17:00").strip()

ENABLE_EMOJIS = os.getenv("ENABLE_EMOJIS", "1").strip() == "1"
SIGNATURE = os.getenv("SIGNATURE", "Equipe zmaximusTraders 🚀").strip()

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "25"))
DEFAULT_COUNTDOWN = int(os.getenv("DEFAULT_COUNTDOWN", "12"))

BASE_STAKE = float(os.getenv("BASE_STAKE", "10"))
PREMIUM_STAKE = float(os.getenv("PREMIUM_STAKE", "20"))
MARTINGALE_MAX = int(os.getenv("MARTINGALE_MAX", "1"))
MARTINGALE_MULT = float(os.getenv("MARTINGALE_MULT", "2"))

PAYOUT_MIN = float(os.getenv("PAYOUT_MIN", "0.80"))
PAYOUT_MAX = float(os.getenv("PAYOUT_MAX", "0.90"))

TARGET_MIN = float(os.getenv("TARGET_MIN", "50"))
TARGET_MAX = float(os.getenv("TARGET_MAX", "100"))

PAUSE_AFTER_CONSEC_LOSSES = int(os.getenv("PAUSE_AFTER_CONSEC_LOSSES", "2"))
PAUSE_MINUTES = int(os.getenv("PAUSE_MINUTES", "30"))
STOP_AFTER_CONSEC_LOSSES = int(os.getenv("STOP_AFTER_CONSEC_LOSSES", "3"))

ABSENT_SCORE_MIN = int(os.getenv("ABSENT_SCORE_MIN", "80"))
ADMIN_TELEGRAM_IDS_RAW = os.getenv("ADMIN_TELEGRAM_IDS", "").strip()

RESULT_DELAY_SECONDS = int(os.getenv("RESULT_DELAY_SECONDS", "75"))
SEND_RESULTS_AUTOMATICALLY = os.getenv("SEND_RESULTS_AUTOMATICALLY", "0").strip() == "1"

# =========================
# TIMEZONE
# =========================
def _get_tz():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except Exception:
        return ZoneInfo("UTC")


_TZ = _get_tz()


def _now_local() -> datetime:
    if _TZ is None:
        return datetime.utcnow()
    return datetime.now(_TZ)


def _now_ts() -> int:
    return int(time.time())


# =========================
# STATE
# =========================
_last_signal_hash = None
_last_signal_ts = 0

PAUSED = False
ABSENT_MODE = False

_day_key = None
profit_est = 0.0
wins = 0
losses = 0
consec_losses = 0
daily_target = None
current_mg_step = 0
last_stake = BASE_STAKE
pause_until_ts = 0

# =========================
# QUEUE
# =========================
_send_queue: "Queue[Tuple[str, Optional[str]]]" = Queue(maxsize=500)


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


def _worker():
    while True:
        try:
            text, chat_id = _send_queue.get()
            ok, msg = _telegram_send_message(text, chat_id)
            print(f"[TG SEND] ok={ok} chat_id={chat_id or TELEGRAM_CHAT_ID} msg={msg}", flush=True)
        except Exception as e:
            print(f"[TG SEND ERROR] {e}", flush=True)
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
        print("[QUEUE FULL] não foi possível enfileirar mensagem", flush=True)


# =========================
# HELPERS
# =========================
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


def _parse_payload() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data

    raw = (request.data or b"").decode("utf-8", errors="ignore").strip()
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                return j
        except Exception:
            pass

    if request.form:
        return dict(request.form)

    return {}


def _require_secret_for_tv(payload: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], int]]:
    if not WEBHOOK_SECRET:
        return None

    secret = str(payload.get("secret", "")).strip()
    if secret == WEBHOOK_SECRET:
        return None

    return {"status": "error", "message": "Secret inválido (WEBHOOK_SECRET)."}, 401


def _parse_windows(s: str) -> List[Tuple[int, int]]:
    windows = []
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    for p in parts:
        if "-" not in p:
            continue
        a, b = [x.strip() for x in p.split("-", 1)]
        try:
            sh, sm = [int(x) for x in a.split(":")]
            eh, em = [int(x) for x in b.split(":")]
            windows.append((sh * 60 + sm, eh * 60 + em))
        except Exception:
            continue
    return windows


_WINDOWS = _parse_windows(TRADING_WINDOWS)


def _in_trading_window(now: datetime) -> bool:
    if not _WINDOWS:
        return True
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
        "score": str(p.get("score", p.get("conf", ""))),
    }

    h = _sha1(json.dumps(core, sort_keys=True))
    now = _now_ts()

    if (now - _last_signal_ts) < COOLDOWN_SECONDS and _last_signal_hash == h:
        return True

    _last_signal_hash = h
    _last_signal_ts = now
    return False


def _admin_ids() -> List[int]:
    ids = []
    for part in ADMIN_TELEGRAM_IDS_RAW.split(","):
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
    return user_id in admins if admins else True


def _reset_day_if_needed(now: datetime):
    global _day_key, profit_est, wins, losses, consec_losses
    global daily_target, current_mg_step, last_stake, pause_until_ts

    dk = now.strftime("%Y-%m-%d")
    if _day_key != dk:
        import random
        _day_key = dk
        profit_est = 0.0
        wins = 0
        losses = 0
        consec_losses = 0
        current_mg_step = 0
        last_stake = BASE_STAKE
        pause_until_ts = 0
        daily_target = float(random.randint(int(TARGET_MIN), int(TARGET_MAX)))


def _is_paused(now: datetime) -> Tuple[bool, str]:
    if PAUSED:
        return True, "pausado_manual"
    if pause_until_ts and _now_ts() < pause_until_ts:
        return True, "pausa_temporaria"
    return False, ""


def _set_temp_pause(minutes: int):
    global pause_until_ts
    pause_until_ts = _now_ts() + int(minutes * 60)


def _profit_on_win(stake: float) -> float:
    payout_mid = (PAYOUT_MIN + PAYOUT_MAX) / 2.0
    return stake * payout_mid


def _format_status(now: datetime) -> str:
    mode = "AUSENTE ✅" if ABSENT_MODE else "NORMAL ▶️"
    paused, why = _is_paused(now)
    ptxt = "SIM ⛔" if paused else "NÃO ✅"
    target = daily_target if daily_target is not None else 0

    extra_pause = ""
    if why == "pausa_temporaria" and pause_until_ts:
        left = max(0, pause_until_ts - _now_ts())
        mins = left // 60
        secs = left % 60
        extra_pause = f"• Retorno em: <b>{mins:02d}:{secs:02d}</b>\n"

    return (
        f"📡 <b>Status V10 Auto-Piloto</b>\n\n"
        f"• Modo: <b>{mode}</b>\n"
        f"• Pausado: <b>{ptxt}</b>\n"
        f"{extra_pause}"
        f"• Agora: <b>{now.strftime('%d/%m %H:%M:%S')}</b>\n"
        f"• Janela OK: <b>{'SIM ✅' if _in_trading_window(now) else 'NÃO ⛔'}</b>\n\n"
        f"• Meta do dia: <b>R${target:.0f}</b>\n"
        f"• Lucro estimado: <b>R${profit_est:.2f}</b>\n"
        f"• Wins/Loss: <b>{wins}/{losses}</b>\n"
        f"• Loss seguidos: <b>{consec_losses}</b>\n"
        f"• Martingale atual: <b>MG{current_mg_step}/{MARTINGALE_MAX}</b>\n"
        f"• Janelas: <b>{TRADING_WINDOWS}</b>\n\n"
        f"<b>{SIGNATURE}</b>"
    )


def _suggest_stake(score: Optional[float]) -> float:
    if score is not None and score >= 80:
        return PREMIUM_STAKE
    return BASE_STAKE


def _format_signal(p: Dict[str, Any], stake: float, score: Optional[float]) -> str:
    ativo = str(p.get("ativo", p.get("symbol", "EURUSD"))).upper()
    direcao = _normalize_direction(str(p.get("acao", p.get("direcao", "CALL"))))
    tf = str(p.get("timeframe", p.get("tempo", "1"))).strip()
    strategy = str(p.get("strategy", p.get("estrategia", "Zmaximus V10"))).strip()
    countdown = int(p.get("countdown", DEFAULT_COUNTDOWN))
    broker = str(p.get("broker", p.get("corretora", "IQ Option / Exnova"))).strip()
    price = str(p.get("preco", p.get("price", ""))).strip()
    ts = _now_local().strftime("%d/%m %H:%M:%S")

    label = "🔥 <b>SINAL PREMIUM</b>" if (score is not None and score >= 80) else "📌 <b>SINAL</b>"

    mg_text = (
        f"\n{_emoji('🎯')} <b>MG1 (se loss):</b> R${stake * MARTINGALE_MULT:.0f}"
        if current_mg_step < MARTINGALE_MAX
        else f"\n{_emoji('🛡️')} <b>MG:</b> desativado (limite atingido)"
    )

    price_text = f"{_emoji('💲')} <b>Preço:</b> {price}\n" if price else ""

    return (
        f"{label} <b>V10</b>\n\n"
        f"{_emoji('💱')} <b>Ativo:</b> {ativo}  |  <b>TF:</b> {tf}m\n"
        f"{_emoji('🎯')} <b>Ação:</b> <b>{direcao}</b>\n"
        f"{price_text}"
        f"{_emoji('💰')} <b>Entrada sugerida:</b> R${stake:.0f}\n"
        f"{_emoji('🏦')} <b>Corretora:</b> {broker}\n"
        f"{_emoji('🧠')} <b>Estratégia:</b> {strategy}\n"
        + (f"{_emoji('📈')} <b>Score:</b> {score:.0f}/100\n" if score is not None else "")
        + f"\n{_emoji('⏳')} <b>Entre em:</b> {countdown}s"
        + mg_text
        + f"\n\n{_emoji('🕒')} <i>{ts}</i>\n<b>{SIGNATURE}</b>"
    )


def _format_result_message(is_win: bool) -> str:
    icon = "✅" if is_win else "❌"
    label = "WIN" if is_win else "LOSS"
    return (
        f"{icon} <b>RESULTADO AUTOMÁTICO</b>\n\n"
        f"• Resultado: <b>{label}</b>\n"
        f"• Lucro estimado: <b>R${profit_est:.2f}</b>\n"
        f"• Wins/Loss: <b>{wins}/{losses}</b>\n\n"
        f"<b>{SIGNATURE}</b>"
    )


def _maybe_pause_on_target():
    global PAUSED
    if daily_target is not None and profit_est >= daily_target:
        PAUSED = True
        _enqueue_telegram(
            f"🏆 <b>META DIÁRIA ATINGIDA</b>\n"
            f"• Meta: <b>R${daily_target:.0f}</b>\n"
            f"• Lucro estimado: <b>R${profit_est:.2f}</b>\n\n"
            f"✅ Bot pausado automaticamente.\n\n"
            f"<b>{SIGNATURE}</b>"
        )


def _apply_result(is_win: bool):
    global profit_est, wins, losses, consec_losses
    global current_mg_step, last_stake, PAUSED

    stake_used = last_stake

    if is_win:
        wins += 1
        consec_losses = 0
        profit_est += _profit_on_win(stake_used)
        current_mg_step = 0
        last_stake = BASE_STAKE
    else:
        losses += 1
        consec_losses += 1
        profit_est -= stake_used
        if current_mg_step < MARTINGALE_MAX:
            current_mg_step += 1
            last_stake = stake_used * MARTINGALE_MULT
        else:
            current_mg_step = 0
            last_stake = BASE_STAKE

    if consec_losses >= STOP_AFTER_CONSEC_LOSSES:
        PAUSED = True
        _enqueue_telegram(
            f"⛔ <b>BOT PAUSADO</b>\n"
            f"Motivo: {consec_losses} losses seguidos.\n\n"
            f"<b>{SIGNATURE}</b>"
        )
    elif consec_losses >= PAUSE_AFTER_CONSEC_LOSSES:
        _set_temp_pause(PAUSE_MINUTES)
        _enqueue_telegram(
            f"⏸️ <b>PAUSA AUTOMÁTICA</b>\n"
            f"Motivo: {consec_losses} losses seguidos.\n"
            f"Duração: {PAUSE_MINUTES} min.\n\n"
            f"<b>{SIGNATURE}</b>"
        )

    _maybe_pause_on_target()


def _auto_result_worker():
    while True:
        try:
            time.sleep(1)
        except Exception:
            pass


threading.Thread(target=_auto_result_worker, daemon=True).start()

# =========================
# CORE TRADINGVIEW HANDLER
# =========================
def _handle_tv_webhook() -> Tuple[Dict[str, Any], int]:
    global last_stake

    now = _now_local()
    _reset_day_if_needed(now)

    payload = _parse_payload()
    if not payload:
        return {"status": "error", "message": "Payload vazio/inválido."}, 200

    sec = _require_secret_for_tv(payload)
    if sec:
        return sec

    if not _in_trading_window(now):
        return {"status": "ignored", "message": "blocked_by_time"}, 200

    paused, why = _is_paused(now)
    if paused:
        return {"status": "ignored", "message": f"paused:{why}"}, 200

    if _is_duplicate(payload):
        return {"status": "ignored", "message": "duplicate"}, 200

    score = None
    for k in ["score", "conf", "confidence"]:
        if k in payload:
            try:
                score = float(payload.get(k))
                break
            except Exception:
                pass

    if ABSENT_MODE and (score is None or score < ABSENT_SCORE_MIN):
        return {"status": "ignored", "message": "absent_mode_filtered"}, 200

    stake = _suggest_stake(score)
    if current_mg_step > 0:
        stake = last_stake

    msg = _format_signal(payload, stake, score)

    ok, erro = _telegram_send_message(msg)
    print(f"TELEGRAM SEND: ok={ok} erro={erro}", flush=True)

    if not ok:
        return {"status": "error", "message": erro}, 500

    if SEND_RESULTS_AUTOMATICALLY:
        def _delayed_result():
            try:
                time.sleep(RESULT_DELAY_SECONDS)
                simulated_win = bool(int(time.time()) % 2)
                _apply_result(simulated_win)
                _enqueue_telegram(_format_result_message(simulated_win))
            except Exception as e:
                print(f"[AUTO RESULT ERROR] {e}", flush=True)

        threading.Thread(target=_delayed_result, daemon=True).start()

    return {"status": "ok"}, 200


# =========================
# ROUTES
# =========================
@app.get("/")
def home():
    return "Bot Online ✅", 200


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/debug/env")
def debug_env():
    tok = TELEGRAM_BOT_TOKEN
    masked = (tok[:8] + "..." + tok[-4:]) if tok else ""
    return jsonify({
        "TELEGRAM_BOT_TOKEN_set": bool(TELEGRAM_BOT_TOKEN),
        "TELEGRAM_BOT_TOKEN_masked": masked,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "ADMIN_TELEGRAM_IDS": ADMIN_TELEGRAM_IDS_RAW,
        "WEBHOOK_SECRET_set": bool(WEBHOOK_SECRET),
        "TIMEZONE": TIMEZONE_NAME,
        "TRADING_WINDOWS": TRADING_WINDOWS,
    }), 200


@app.get("/test-send")
def test_send():
    ok, msg = _telegram_send_message("✅ TESTE DIRETO — mensagem enviada sem fila.")
    return jsonify({
        "ok": ok,
        "telegram_result": msg,
        "chat_id": TELEGRAM_CHAT_ID
    }), (200 if ok else 500)


@app.get("/test-me")
def test_me():
    chat_id = request.args.get("chat_id", "").strip()
    if not chat_id:
        return jsonify({"ok": False, "error": "Passe ?chat_id=SEU_CHAT_ID"}), 400

    _enqueue_telegram("✅ TESTE: enviando para seu privado (chat_id informado)...", chat_id)
    return jsonify({"ok": True, "sent_to": chat_id}), 200


@app.post("/tv")
def tv():
    data, code = _handle_tv_webhook()
    return jsonify(data), code


@app.post("/webhook")
def webhook():
    data, code = _handle_tv_webhook()
    return jsonify(data), code


@app.post("/")
def webhook_root():
    data, code = _handle_tv_webhook()
    return jsonify(data), code


# =========================
# TELEGRAM WEBHOOK
# =========================
@app.post("/telegram")
def telegram_webhook():
    global PAUSED, ABSENT_MODE

    now = _now_local()
    _reset_day_if_needed(now)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}

    chat_id = chat.get("id")
    user_id = from_user.get("id")

    print(f"[TG IN] chat_id={chat_id} user_id={user_id} text={text}", flush=True)

    if not text:
        return jsonify({"ok": True})

    if not _is_admin(user_id):
        if chat_id:
            _enqueue_telegram("⛔ Você não tem permissão para esse comando.", str(chat_id))
        return jsonify({"ok": True})

    cmd = text.split()[0].lower()

    if cmd in ["/start", "/help", "/ajuda"]:
        _enqueue_telegram(
            "📌 <b>Comandos</b>\n"
            "• /status\n"
            "• /ausente_on | /ausente_off\n"
            "• /pause | /resume\n"
            "• /win | /loss\n"
            "• /meta\n"
            f"\n<b>{SIGNATURE}</b>",
            str(chat_id),
        )

    elif cmd in ["/pause", "/pausar"]:
        PAUSED = True
        _enqueue_telegram("⏸️ <b>Bot pausado</b>\nUse /resume para voltar.", str(chat_id))

    elif cmd in ["/resume", "/voltar"]:
        PAUSED = False
        _enqueue_telegram("▶️ <b>Bot ativado</b>\nOperação retomada.", str(chat_id))

    elif cmd in ["/ausente_on", "/ausente"]:
        ABSENT_MODE = True
        _enqueue_telegram("🫥 <b>Modo AUSENTE ativado</b>\nVou enviar apenas sinais PREMIUM.", str(chat_id))

    elif cmd in ["/ausente_off", "/normal"]:
        ABSENT_MODE = False
        _enqueue_telegram("✅ <b>Modo NORMAL ativado</b>\nVou enviar sinais normais + premium.", str(chat_id))

    elif cmd == "/status":
        _enqueue_telegram(_format_status(now), str(chat_id))

    elif cmd in ["/win", "/w"]:
        _apply_result(True)
        _enqueue_telegram(_format_result_message(True), str(chat_id))

    elif cmd in ["/loss", "/l"]:
        _apply_result(False)
        _enqueue_telegram(_format_result_message(False), str(chat_id))

    elif cmd == "/meta":
        tgt = daily_target if daily_target is not None else 0
        _enqueue_telegram(
            f"🎯 Meta do dia: <b>R${tgt:.0f}</b>\n"
            f"Lucro est.: <b>R${profit_est:.2f}</b>",
            str(chat_id),
        )

    else:
        _enqueue_telegram(
            "Comandos: /start /status /ausente_on /ausente_off /pause /resume /win /loss /meta",
            str(chat_id),
        )

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)

