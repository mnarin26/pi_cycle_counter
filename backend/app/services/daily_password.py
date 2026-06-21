"""Daily one-time login passwords issued via Telegram bot."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DailyPassword

# Europe/Istanbul is UTC+3, no DST since 2016.
_TZ = timezone(timedelta(hours=3))
_ALPHABET = string.ascii_uppercase + string.digits
_PASSWORD_LEN = 8


def today_str() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _generate_plain() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_PASSWORD_LEN))


def issue_daily_password(db: Session, telegram_user_id: str) -> str:
    """Generate a fresh daily password for the user, invalidating earlier ones today.

    Returns the plain-text password (shown once in Telegram)."""
    uid = str(telegram_user_id).strip()
    day = today_str()
    plain = _generate_plain()
    db.execute(
        delete(DailyPassword).where(
            DailyPassword.telegram_user_id == uid,
            DailyPassword.valid_date == day,
        )
    )
    db.add(
        DailyPassword(
            telegram_user_id=uid,
            password_hash=_hash_password(plain),
            valid_date=day,
        )
    )
    db.commit()
    return plain


def match_daily_password(db: Session, password: str) -> str | None:
    """Return the telegram_user_id whose today's password matches, else None."""
    if not password:
        return None
    day = today_str()
    target = _hash_password(password)
    rows = (
        db.query(DailyPassword)
        .filter(DailyPassword.valid_date == day)
        .all()
    )
    for r in rows:
        if hmac.compare_digest(r.password_hash, target):
            return r.telegram_user_id
    return None


def purge_old_passwords(db: Session) -> None:
    day = today_str()
    db.execute(delete(DailyPassword).where(DailyPassword.valid_date != day))
    db.commit()
