"""Background dispatcher: diagnosis.db queue → Telegram sendMessage.

Runs in the bot process so vision threads never wait on HTTP.
"""

from __future__ import annotations

import logging
import threading
import time

from app.services import fault_log
from app.services.alert_notify import format_detail_message, format_headline, recipients_for_alert
from app.services.stored_settings import get_telegram_config
import app.db.session as db_session

logger = logging.getLogger(__name__)

_started = False
_POLL_S = 1.0


def _send_one(send_message, token: str, alert: dict) -> None:
    headline = format_headline(
        alert.get("machine_name"),
        alert.get("title_tr") or alert.get("name"),
        machine_id=alert.get("machine_id"),
    )
    detail_text = format_detail_message(str(alert.get("code") or ""), alert.get("detail"))
    db = db_session.SessionLocal()
    try:
        cfg = get_telegram_config(db)
    finally:
        db.close()
    recipients = recipients_for_alert(cfg)
    for rec in recipients:
        chat_id = int(rec["telegram_user_id"])
        try:
            send_message(token, chat_id, headline)
            if rec.get("include_details") and detail_text:
                send_message(token, chat_id, detail_text)
        except Exception:
            logger.exception(
                "alert send failed uid=%s alarm=%s", rec.get("telegram_user_id"), alert.get("id")
            )


def dispatch_pending_alerts(send_message) -> int:
    """Send queued diagnosis alerts. Returns number of queue rows processed."""
    db = db_session.SessionLocal()
    try:
        cfg = get_telegram_config(db)
    finally:
        db.close()
    if not cfg.get("enabled"):
        return 0
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        return 0
    pending = fault_log.list_pending_alerts(limit=20)
    if not pending:
        return 0
    done: list[int] = []
    for alert in pending:
        try:
            _send_one(send_message, token, alert)
        except Exception:
            logger.exception("alert dispatch failed id=%s", alert.get("id"))
        done.append(int(alert["id"]))
    if done:
        fault_log.mark_alerts_done(done)
    return len(done)


def _loop(send_message) -> None:
    while True:
        try:
            dispatch_pending_alerts(send_message)
        except Exception:
            logger.exception("alert dispatcher loop")
        time.sleep(_POLL_S)


def start_alert_thread(send_message) -> None:
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_loop, args=(send_message,), name="tg-diag-alerts", daemon=True)
    t.start()
    logger.info("Diagnosis Telegram alert dispatcher started")
