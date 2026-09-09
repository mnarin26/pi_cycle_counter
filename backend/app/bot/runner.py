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
from app.services.daily_password import issue_daily_password
from app.services import audit_log

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class BotState(str, Enum):
    IDLE = "idle"
    ASSIGN_MACHINE = "assign_machine"
    ASSIGN_MOLD = "assign_mold"
    CREATE_MOLD_QR = "create_mold_qr"
    CREATE_MOLD_NAME = "create_mold_name"
    CREATE_MOLD_TARGET = "create_mold_target"
    CREATE_MOLD_DAILY = "create_mold_daily"
    CREATE_MOLD_MODE = "create_mold_mode"
    CREATE_MOLD_MOUNT = "create_mold_mount"
    CREATE_MOLD_REMOVAL = "create_mold_removal"


CREATE_TEXT_STATES = {
    BotState.CREATE_MOLD_NAME,
    BotState.CREATE_MOLD_TARGET,
    BotState.CREATE_MOLD_DAILY,
    BotState.CREATE_MOLD_MODE,
    BotState.CREATE_MOLD_MOUNT,
    BotState.CREATE_MOLD_REMOVAL,
}


@dataclass
class UserSession:
    state: BotState = BotState.IDLE
    machine_id: int | None = None
    machine_name: str | None = None
    pending_qr_code: str | None = None
    pending_name: str | None = None
    pending_target_cycle_s: float | None = None
    pending_daily_target: int | None = None
    pending_work_mode: str = "auto"
    pending_mount_minutes: int | None = None
    pending_removal_minutes: int | None = None


@dataclass
class BotRuntime:
    offset: int = 0
    sessions: dict[str, UserSession] = field(default_factory=dict)


def _session(user_id: str) -> UserSession:
    return _runtime.sessions.setdefault(user_id, UserSession())


_runtime = BotRuntime()


def _http_client() -> httpx.Client:
    # Pi often has AAAA for api.telegram.org but no working global IPv6 route
    # → ConnectError [Errno 101] Network is unreachable. Force IPv4.
    return httpx.Client(
        timeout=60.0,
        transport=httpx.HTTPTransport(local_address="0.0.0.0"),
    )


def _api(token: str, method: str, **payload) -> dict[str, Any]:
    url = API_BASE.format(token=token, method=method)
    with _http_client() as client:
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


def _main_keyboard(operator) -> dict:
    rows = []
    if can_assign(operator):
        rows.append(["📌 Kalıp Ata"])
    if can_create_mold(operator):
        rows.append(["➕ Kalıp Üret"])
    if _can_panel(operator):
        rows.append(["🔑 Şifre İste"])
    rows.append(["❌ İptal"])
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": False}


def _can_panel(operator) -> bool:
    return operator.has("panel_8000") or operator.has("panel_8080")


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
    role_label = "Yönetici" if operator.role == "admin" else "Kullanıcı"
    send_message(
        token,
        chat_id,
        f"Merhaba {operator.name}.\nRol: {role_label}\n\nMenüden işlem seçin.",
        reply_markup=_main_keyboard(operator),
    )


def handle_cancel(token: str, chat_id: int, user_id: str, operator) -> None:
    reset_session(user_id)
    send_message(
        token,
        chat_id,
        "İptal edildi.",
        reply_markup=_main_keyboard(operator),
    )


def handle_password_request(token: str, chat_id: int, user_id: str, operator) -> None:
    if not _can_panel(operator):
        send_message(token, chat_id, "Panel erişiminiz yok; şifre üretemezsiniz.")
        return
    db = db_session.SessionLocal()
    try:
        tg = (operator.telegram_user_id or operator.id or "").strip()
        if not tg.isdigit():
            send_message(token, chat_id, "Telegram ID tanimli degil; uzaktan gunluk sifre uretilemez.")
            return
        plain = issue_daily_password(db, tg)
        audit_log.log_action(
            db,
            actor_type="operator",
            action="auth.daily_password_issued",
            actor_name=operator.name,
            telegram_user_id=tg,
        )
    finally:
        db.close()
    try:
        send_message(
            token,
            chat_id,
            (
                f"🔑 Bugünkü giriş şifreniz:\n\n{plain}\n\n"
                "Bu şifre bugün geçerlidir. Panele kendi adınız + bu şifre ile "
                "hem fabrikadan hem uzaktan girebilirsiniz. Yeni şifre isterseniz eskisi geçersiz olur."
            ),
            reply_markup=_main_keyboard(operator),
        )
    except Exception:
        logger.exception("daily password issued but Telegram send failed uid=%s", user_id)
        # Best-effort retry once (transient IPv6/route blips)
        try:
            time.sleep(1.0)
            send_message(
                token,
                chat_id,
                (
                    f"🔑 Bugünkü giriş şifreniz:\n\n{plain}\n\n"
                    "Bu şifre bugün geçerlidir. Panele kendi adınız + bu şifre ile "
                    "hem fabrikadan hem uzaktan girebilirsiniz. Yeni şifre isterseniz eskisi geçersiz olur."
                ),
                reply_markup=_main_keyboard(operator),
            )
        except Exception:
            logger.exception("daily password send retry failed uid=%s", user_id)


def _skip_keyboard() -> dict:
    return {
        "keyboard": [["- Atla"], ["❌ İptal"]],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _mode_keyboard() -> dict:
    return {
        "keyboard": [["Otomatik", "Manuel"], ["❌ İptal"]],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _is_skip(text: str) -> bool:
    n = _norm_cmd(text)
    return n in {"-", "atla", "- atla", "gec", "yok", "skip", "bos", "."}


def _parse_positive_float(text: str) -> float:
    v = float(text.strip().replace(",", "."))
    if not (v > 0):
        raise ValueError("Pozitif bir sayı yazın")
    return v


def _parse_nonneg_int(text: str) -> int:
    v = int(float(text.strip().replace(",", ".")))
    if v < 0:
        raise ValueError("0 veya daha büyük bir sayı yazın")
    return v


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
    if _match_cmd(t, "🔑 Şifre İste", "Şifre İste", "/sifre", "sifre iste"):
        handle_password_request(token, chat_id, user_id, operator)
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
            send_message(token, chat_id, "Kalıp üretme yetkiniz yok.")
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
    if sess.state in CREATE_TEXT_STATES:
        _advance_create(token, chat_id, user_id, operator, t)
        return
    if sess.state == BotState.IDLE:
        send_message(token, chat_id, "Menüden seçim yapın veya /start yazın.", reply_markup=_main_keyboard(operator))


def _advance_create(token: str, chat_id: int, user_id: str, operator, text: str) -> None:
    sess = _session(user_id)
    t = (text or "").strip()

    if sess.state == BotState.CREATE_MOLD_NAME:
        if not t or _is_skip(t):
            send_message(token, chat_id, "Kalıp adı boş olamaz. Adı yazın.")
            return
        sess.pending_name = t
        sess.state = BotState.CREATE_MOLD_TARGET
        send_message(
            token,
            chat_id,
            "Hedef çalışma süresi (saniye).\nYoksa - Atla yazın.",
            reply_markup=_skip_keyboard(),
        )
        return

    if sess.state == BotState.CREATE_MOLD_TARGET:
        if _is_skip(t):
            sess.pending_target_cycle_s = None
        else:
            try:
                sess.pending_target_cycle_s = _parse_positive_float(t)
            except ValueError:
                send_message(token, chat_id, "Geçerli bir süre yazın (örn. 12.5) veya - Atla.")
                return
        sess.state = BotState.CREATE_MOLD_DAILY
        send_message(
            token,
            chat_id,
            "Günlük hedef baskı adedi.\nYoksa - Atla yazın.",
            reply_markup=_skip_keyboard(),
        )
        return

    if sess.state == BotState.CREATE_MOLD_DAILY:
        if _is_skip(t):
            sess.pending_daily_target = None
        else:
            try:
                n = _parse_nonneg_int(t)
                if n <= 0:
                    raise ValueError("pozitif")
                sess.pending_daily_target = n
            except ValueError:
                send_message(token, chat_id, "Geçerli bir adet yazın (örn. 15000) veya - Atla.")
                return
        sess.state = BotState.CREATE_MOLD_MODE
        send_message(
            token,
            chat_id,
            "Çalışma modu: Otomatik veya Manuel.\n(Manuelde vardiya molaları verimlilikten düşülür.)",
            reply_markup=_mode_keyboard(),
        )
        return

    if sess.state == BotState.CREATE_MOLD_MODE:
        n = _norm_cmd(t)
        if n in {"otomatik", "auto"}:
            sess.pending_work_mode = "auto"
        elif n in {"manuel", "manual"}:
            sess.pending_work_mode = "manual"
        else:
            send_message(token, chat_id, "Otomatik veya Manuel seçin.", reply_markup=_mode_keyboard())
            return
        sess.state = BotState.CREATE_MOLD_MOUNT
        send_message(
            token,
            chat_id,
            "Montaj süresi (dakika).\nYoksa - Atla yazın.",
            reply_markup=_skip_keyboard(),
        )
        return

    if sess.state == BotState.CREATE_MOLD_MOUNT:
        if _is_skip(t):
            sess.pending_mount_minutes = None
        else:
            try:
                sess.pending_mount_minutes = _parse_nonneg_int(t)
            except ValueError:
                send_message(token, chat_id, "Geçerli bir dakika yazın (örn. 30) veya - Atla.")
                return
        sess.state = BotState.CREATE_MOLD_REMOVAL
        send_message(
            token,
            chat_id,
            "Sökme süresi (dakika).\nYoksa - Atla yazın.",
            reply_markup=_skip_keyboard(),
        )
        return

    if sess.state == BotState.CREATE_MOLD_REMOVAL:
        if _is_skip(t):
            sess.pending_removal_minutes = None
        else:
            try:
                sess.pending_removal_minutes = _parse_nonneg_int(t)
            except ValueError:
                send_message(token, chat_id, "Geçerli bir dakika yazın (örn. 20) veya - Atla.")
                return
        _commit_create(token, chat_id, user_id, operator)
        return


def _commit_create(token: str, chat_id: int, user_id: str, operator) -> None:
    sess = _session(user_id)
    code = sess.pending_qr_code
    name = sess.pending_name
    if not code or not name:
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
            target_cycle_s=sess.pending_target_cycle_s,
            daily_target_count=sess.pending_daily_target,
            work_mode=sess.pending_work_mode,
            mount_minutes=sess.pending_mount_minutes,
            removal_minutes=sess.pending_removal_minutes,
        )
        audit_log.log_action(
            db,
            actor_type="operator",
            action="mold.create",
            actor_name=operator.name,
            telegram_user_id=operator.id,
            resource=f"mold/{mold.id}",
            detail={"qr_code": mold.qr_code, "name": mold.name},
        )
        reset_session(user_id)
        mode_tr = "Manuel" if mold.work_mode == "manual" else "Otomatik"
        bits = [
            f"✅ Kalıp kaydedildi.",
            f"Kod: {mold.qr_code}",
            f"Ad: {mold.name}",
            f"Mod: {mode_tr}",
        ]
        if mold.target_cycle_s:
            bits.append(f"Hedef süre: {mold.target_cycle_s:.2f}s")
        if mold.daily_target_count:
            bits.append(f"Günlük hedef: {mold.daily_target_count}")
        if mold.mount_minutes:
            bits.append(f"Montaj: {mold.mount_minutes} dk")
        if mold.removal_minutes:
            bits.append(f"Sökme: {mold.removal_minutes} dk")
        bits.append("\n8000/Kalıplar sayfasında görünür.")
        send_message(
            token,
            chat_id,
            "\n".join(bits),
            reply_markup=_main_keyboard(operator),
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
            audit_log.log_action(
                db,
                actor_type="operator",
                action="mold.assign",
                actor_name=operator.name,
                telegram_user_id=operator.id,
                resource=f"machine/{machine.id}",
                detail={
                    "machine_id": machine.id,
                    "machine_name": machine.name,
                    "machine": machine.name,
                    "mold_id": mold.id,
                    "mold_name": mold.name,
                    "mold": mold.name or mold.qr_code,
                    "mold_qr_code": mold.qr_code,
                    "source": "telegram",
                },
            )
            reset_session(user_id)
            label = mold.name or mold.qr_code or str(mold.id)
            send_message(
                token,
                chat_id,
                f"✅ Atama tamam.\n{machine.name} → {label}\n(Kod: {mold.qr_code or '—'})",
                reply_markup=_main_keyboard(operator),
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
                    reply_markup=_main_keyboard(operator),
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
                reply_markup=_main_keyboard(operator),
            )
        return

    if sess.state in CREATE_TEXT_STATES:
        if text:
            _advance_create(token, chat_id, user_id, operator, text)
        else:
            send_message(token, chat_id, "Bu adımda metin bekleniyor. Fotoğraf değil, yazı gönderin. /iptal ile çıkın.")
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
