"""Telegram bot: QR-based mold assign and create with operator levels."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

import app.db.session as db_session
from app.services.mold_registry import (
    assign_mold_to_machine,
    create_mold_from_qr,
    find_mold_by_qr_code,
    parse_and_resolve_machine,
    parse_and_resolve_mold,
)
from app.services.qr_codec import QrKind, decode_qr_from_image_bytes, parse_qr_text
from app.services.stored_settings import get_telegram_config
from app.services.telegram_auth import can_assign, can_create_mold, get_operator

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class BotState(str, Enum):
    IDLE = "idle"
    ASSIGN_MACHINE = "assign_machine"
    ASSIGN_MOLD = "assign_mold"
    CREATE_MOLD_QR = "create_mold_qr"
    CREATE_MOLD_NAME = "create_mold_name"


@dataclass
class UserSession:
    state: BotState = BotState.IDLE
    machine_id: int | None = None
    machine_name: str | None = None
    pending_qr_code: str | None = None


@dataclass
class BotRuntime:
    offset: int = 0
    sessions: dict[str, UserSession] = field(default_factory=dict)


def _session(user_id: str) -> UserSession:
    return _runtime.sessions.setdefault(user_id, UserSession())


_runtime = BotRuntime()


def _api(token: str, method: str, **payload) -> dict[str, Any]:
    url = API_BASE.format(token=token, method=method)
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "Telegram API hatasi")
    return data


def send_message(token: str, chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    body: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        body["reply_markup"] = reply_markup
    _api(token, "sendMessage", **body)


def _main_keyboard(level: int) -> dict:
    rows = [["📌 Kalıp Ata"]]
    if level == 1:
        rows.append(["➕ Kalıp Üret"])
    rows.append(["❌ İptal"])
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": False}


def _remove_keyboard(token: str, chat_id: int, text: str) -> None:
    send_message(token, chat_id, text, reply_markup={"remove_keyboard": True})


def _download_photo_bytes(token: str, file_id: str) -> bytes:
    meta = _api(token, "getFile", file_id=file_id)
    path = meta["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{path}"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def _decode_message_qr(token: str, message: dict[str, Any]) -> str:
    if message.get("text"):
        return str(message["text"]).strip()
    photos = message.get("photo") or []
    if photos:
        best = photos[-1]
        data = _download_photo_bytes(token, best["file_id"])
        return decode_qr_from_image_bytes(data)
    raise ValueError("Metin veya QR fotoğrafı gönderin")


def reset_session(user_id: str) -> None:
    _runtime.sessions[user_id] = UserSession()


def handle_start(token: str, chat_id: int, user_id: str, operator) -> None:
    reset_session(user_id)
    level_label = "Seviye 1 — atama + üretim" if operator.level == 1 else "Seviye 2 — sadece atama"
    send_message(
        token,
        chat_id,
        f"Merhaba {operator.name}.\n{level_label}\n\nMenüden işlem seçin.",
        reply_markup=_main_keyboard(operator.level),
    )


def handle_cancel(token: str, chat_id: int, user_id: str, operator) -> None:
    reset_session(user_id)
    send_message(
        token,
        chat_id,
        "İptal edildi.",
        reply_markup=_main_keyboard(operator.level),
    )


def _norm_cmd(text: str) -> str:
    t = (text or "").strip().casefold()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        t = t.replace(a, b)
    return t


def _match_cmd(text: str, *variants: str) -> bool:
    n = _norm_cmd(text)
    return any(_norm_cmd(v) == n for v in variants)


def handle_text_command(token: str, chat_id: int, user_id: str, operator, text: str) -> None:
    t = text.strip()
    if _match_cmd(t, "/start", "start"):
        handle_start(token, chat_id, user_id, operator)
        return
    if _match_cmd(t, "/iptal", "❌ İptal", "İptal", "iptal"):
        handle_cancel(token, chat_id, user_id, operator)
        return
    if _match_cmd(t, "📌 Kalıp Ata", "Kalıp Ata", "/ata", "kalip ata"):
        if not can_assign(operator):
            send_message(token, chat_id, "Bu işlem için yetkiniz yok.")
            return
        sess = _session(user_id)
        sess.state = BotState.ASSIGN_MACHINE
        sess.machine_id = None
        send_message(
            token,
            chat_id,
            "1/2 — Makine QR fotoğrafını gönderin.\n(Plakada MACHINE:3 veya MAKINE:3 formatı)",
            reply_markup={"remove_keyboard": True},
        )
        return
    if _match_cmd(t, "➕ Kalıp Üret", "Kalıp Üret", "/uret", "kalip uret", "KALIP URET"):
        if not can_create_mold(operator):
            send_message(token, chat_id, "Kalıp üretme sadece 1. seviye operatörler içindir.")
            return
        sess = _session(user_id)
        sess.state = BotState.CREATE_MOLD_QR
        send_message(
            token,
            chat_id,
            "Kalıp QR fotoğrafını gönderin.\n(Plakada MOLD:042 veya KALIP:042 formatı)",
            reply_markup={"remove_keyboard": True},
        )
        return

    sess = _session(user_id)
    if sess.state == BotState.CREATE_MOLD_NAME:
        _finish_create_name(token, chat_id, user_id, operator, t)
        return
    if sess.state == BotState.IDLE:
        send_message(token, chat_id, "Menüden seçim yapın veya /start yazın.", reply_markup=_main_keyboard(operator.level))


def _finish_create_name(token: str, chat_id: int, user_id: str, operator, name: str) -> None:
    sess = _session(user_id)
    code = sess.pending_qr_code
    if not code:
        reset_session(user_id)
        send_message(token, chat_id, "Oturum süresi doldu. /start ile tekrar deneyin.")
        return
    db = db_session.SessionLocal()
    try:
        mold = create_mold_from_qr(
            db,
            qr_code=code,
            name=name,
            operator_name=operator.name,
        )
        reset_session(user_id)
        send_message(
            token,
            chat_id,
            f"✅ Kalıp kaydedildi.\nKod: {mold.qr_code}\nAd: {mold.name}\n\n8000/Kalıplar sayfasında görünür.",
            reply_markup=_main_keyboard(operator.level),
        )
    except ValueError as e:
        send_message(token, chat_id, f"Hata: {e}")
    finally:
        db.close()


def handle_qr_or_text(token: str, chat_id: int, user_id: str, operator, message: dict[str, Any]) -> None:
    sess = _session(user_id)
    if sess.state == BotState.IDLE:
        if message.get("text"):
            handle_text_command(token, chat_id, user_id, operator, message["text"])
        return

    try:
        raw = _decode_message_qr(token, message)
    except ValueError as e:
        send_message(token, chat_id, str(e))
        return

    db = db_session.SessionLocal()
    try:
        if sess.state == BotState.ASSIGN_MACHINE:
            machine = parse_and_resolve_machine(db, raw)
            sess.machine_id = machine.id
            sess.machine_name = machine.name
            sess.state = BotState.ASSIGN_MOLD
            send_message(
                token,
                chat_id,
                f"Makine: {machine.name} (ID {machine.id})\n\n2/2 — Kalıp QR fotoğrafını gönderin.",
            )
            return

        if sess.state == BotState.ASSIGN_MOLD:
            if sess.machine_id is None:
                reset_session(user_id)
                send_message(token, chat_id, "Oturum hatası. /start ile tekrar deneyin.")
                return
            payload = parse_qr_text(raw)
            if payload.kind == QrKind.MACHINE:
                raise ValueError("Kalıp QR bekleniyor; makine QR gönderdiniz.")
            if payload.kind != QrKind.MOLD:
                payload = parse_qr_text(f"MOLD:{payload.code}")
            mold = parse_and_resolve_mold(db, payload.raw)
            machine, mold = assign_mold_to_machine(
                db,
                machine_id=sess.machine_id,
                mold_id=mold.id,
                operator_name=operator.name,
                operator_id=operator.id,
            )
            reset_session(user_id)
            label = mold.name or mold.qr_code or str(mold.id)
            send_message(
                token,
                chat_id,
                f"✅ Atama tamam.\n{machine.name} → {label}\n(Kod: {mold.qr_code or '—'})",
                reply_markup=_main_keyboard(operator.level),
            )
            return

        if sess.state == BotState.CREATE_MOLD_QR:
            payload = parse_qr_text(raw)
            if payload.kind == QrKind.MACHINE:
                raise ValueError("Kalıp QR bekleniyor; makine QR gönderdiniz.")
            if payload.kind != QrKind.MOLD:
                payload = parse_qr_text(f"MOLD:{payload.code}")
            code = payload.code.strip()
            existing = find_mold_by_qr_code(db, code)
            if existing:
                nm = existing.name or existing.qr_code
                reset_session(user_id)
                send_message(
                    token,
                    chat_id,
                    f"Bu QR zaten kayıtlı: {nm} (kod {existing.qr_code}).",
                    reply_markup=_main_keyboard(operator.level),
                )
                return
            sess.pending_qr_code = code
            sess.state = BotState.CREATE_MOLD_NAME
            send_message(token, chat_id, f"Yeni kod: {code}\n\nKalıp adını yazın (ör. Kapak A):")
            return
    except ValueError as e:
        send_message(token, chat_id, f"Hata: {e}")
    except Exception as e:
        logger.exception("handle_qr_or_text")
        send_message(token, chat_id, f"Islem basarisiz: {e}")
    finally:
        db.close()


def process_update(token: str, update: dict[str, Any]) -> None:
    message = update.get("message")
    if not message:
        return
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    user_id = str(from_user.get("id") or "")
    if not chat_id or not user_id:
        return

    db = db_session.SessionLocal()
    try:
        cfg = get_telegram_config(db)
        if not cfg.get("enabled"):
            return
        operator = get_operator(db, user_id)
        if operator is None:
            send_message(token, chat_id, "Yetkisiz. Admin panelinden operatör olarak eklenmelisiniz.")
            return
    finally:
        db.close()

    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        handle_start(token, chat_id, user_id, operator)
        return

    if _match_cmd(text, "/iptal", "❌ İptal", "İptal", "iptal"):
        handle_cancel(token, chat_id, user_id, operator)
        return

    sess = _session(user_id)

    if sess.state == BotState.IDLE:
        if text:
            handle_text_command(token, chat_id, user_id, operator, text)
        elif message.get("photo"):
            send_message(
                token,
                chat_id,
                "Once menuden islem secin:\n📌 Kalip Ata veya ➕ Kalip Uret\n\n/start ile menuyu acin.",
                reply_markup=_main_keyboard(operator.level),
            )
        return

    if sess.state == BotState.CREATE_MOLD_NAME:
        if text:
            _finish_create_name(token, chat_id, user_id, operator, text)
        else:
            send_message(token, chat_id, "Kalıp adını yazın.")
        return

    handle_qr_or_text(token, chat_id, user_id, operator, message)


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO)
    db_session.init_db()
    logger.info("Telegram bot baslatiliyor...")
    while True:
        db = db_session.SessionLocal()
        try:
            cfg = get_telegram_config(db)
            if not cfg.get("enabled"):
                time.sleep(5)
                continue
            token = (cfg.get("bot_token") or "").strip()
            if not token:
                time.sleep(5)
                continue
        finally:
            db.close()

        try:
            data = _api(token, "getUpdates", offset=_runtime.offset, timeout=30)
            for upd in data.get("result") or []:
                _runtime.offset = max(_runtime.offset, int(upd["update_id"]) + 1)
                try:
                    process_update(token, upd)
                except Exception:
                    logger.exception("update islenemedi")
                    try:
                        msg = upd.get("message") or {}
                        cid = (msg.get("chat") or {}).get("id")
                        if cid:
                            send_message(token, cid, "Beklenmeyen hata. /iptal sonra tekrar deneyin.")
                    except Exception:
                        logger.exception("hata mesaji gonderilemedi")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                logger.error("409 Conflict: baska bot instance calisiyor — fazlalari durdurun")
            logger.exception("Telegram HTTP hatasi")
            time.sleep(5)
        except Exception:
            logger.exception("bot dongusu")
            time.sleep(5)


if __name__ == "__main__":
    run_forever()
