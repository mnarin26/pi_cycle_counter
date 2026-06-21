"""Web session auth: super password + Telegram-issued daily passwords."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import WebSession
from app.services.daily_password import match_daily_password
from app.services.stored_settings import PERMISSION_KEYS, get_operator_record

_SUPER_PERMISSIONS = {k: True for k in PERMISSION_KEYS}

# Simple in-memory brute-force guard: ip -> list[timestamp]
_failed_attempts: dict[str, list[float]] = {}
_MAX_FAILS = 5
_WINDOW_S = 15 * 60


@dataclass
class CurrentUser:
    actor_type: str  # super, operator
    telegram_user_id: str | None
    display_name: str
    permissions: dict[str, bool]

    def has(self, perm: str) -> bool:
        return bool(self.permissions.get(perm, False))

    @property
    def is_super(self) -> bool:
        return self.actor_type == "super"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_failure(ip: str | None) -> None:
    if not ip:
        return
    now = time.time()
    bucket = [t for t in _failed_attempts.get(ip, []) if now - t < _WINDOW_S]
    bucket.append(now)
    _failed_attempts[ip] = bucket


def is_locked_out(ip: str | None) -> bool:
    if not ip:
        return False
    now = time.time()
    bucket = [t for t in _failed_attempts.get(ip, []) if now - t < _WINDOW_S]
    _failed_attempts[ip] = bucket
    return len(bucket) >= _MAX_FAILS


def clear_failures(ip: str | None) -> None:
    if ip:
        _failed_attempts.pop(ip, None)


def _create_session(
    db: Session,
    *,
    actor_type: str,
    telegram_user_id: str | None,
    display_name: str,
    permissions: dict[str, bool],
) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(hours=settings.session_max_hours)
    db.add(
        WebSession(
            session_token=token,
            actor_type=actor_type,
            telegram_user_id=telegram_user_id,
            display_name=display_name,
            permissions_json=json.dumps(permissions, ensure_ascii=False),
            expires_at=expires,
            last_seen_at=now,
        )
    )
    db.commit()
    return token


def login(db: Session, password: str) -> tuple[str, CurrentUser] | None:
    """Validate password, create session. Returns (token, user) or None."""
    pw = (password or "").strip()
    if not pw:
        return None

    if secrets.compare_digest(pw, settings.super_password):
        user = CurrentUser(
            actor_type="super",
            telegram_user_id=None,
            display_name="Super Kullanici",
            permissions=dict(_SUPER_PERMISSIONS),
        )
        token = _create_session(
            db,
            actor_type="super",
            telegram_user_id=None,
            display_name=user.display_name,
            permissions=user.permissions,
        )
        return token, user

    uid = match_daily_password(db, pw)
    if uid:
        rec = get_operator_record(db, uid)
        if rec is None:
            return None
        perms = dict(rec.get("permissions") or {})
        user = CurrentUser(
            actor_type="operator",
            telegram_user_id=uid,
            display_name=rec.get("name") or uid,
            permissions={k: bool(perms.get(k, False)) for k in PERMISSION_KEYS},
        )
        token = _create_session(
            db,
            actor_type="operator",
            telegram_user_id=uid,
            display_name=user.display_name,
            permissions=user.permissions,
        )
        return token, user

    return None


def get_session_user(db: Session, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    row = db.query(WebSession).filter(WebSession.session_token == token).first()
    if row is None:
        return None
    now = _now()
    expires_at = row.expires_at
    last_seen = row.last_seen_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if last_seen and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    # Absolute and idle expiry
    if expires_at and now > expires_at:
        _delete_token(db, token)
        return None
    if last_seen and now - last_seen > timedelta(hours=settings.session_idle_hours):
        _delete_token(db, token)
        return None

    # For operator sessions, re-check the live permission set (revocation support)
    if row.actor_type == "operator" and row.telegram_user_id:
        rec = get_operator_record(db, row.telegram_user_id)
        if rec is None:
            _delete_token(db, token)
            return None
        perms = {k: bool((rec.get("permissions") or {}).get(k, False)) for k in PERMISSION_KEYS}
        display_name = rec.get("name") or row.telegram_user_id
    else:
        try:
            perms = json.loads(row.permissions_json) if row.permissions_json else {}
        except json.JSONDecodeError:
            perms = {}
        perms = {k: bool(perms.get(k, True)) for k in PERMISSION_KEYS}
        display_name = row.display_name

    row.last_seen_at = now
    db.commit()

    return CurrentUser(
        actor_type=row.actor_type,
        telegram_user_id=row.telegram_user_id,
        display_name=display_name,
        permissions=perms,
    )


def _delete_token(db: Session, token: str) -> None:
    db.execute(delete(WebSession).where(WebSession.session_token == token))
    db.commit()


def logout(db: Session, token: str | None) -> None:
    if token:
        _delete_token(db, token)


def invalidate_operator_sessions(db: Session, telegram_user_id: str) -> None:
    db.execute(delete(WebSession).where(WebSession.telegram_user_id == str(telegram_user_id).strip()))
    db.commit()


def purge_expired_sessions(db: Session) -> None:
    db.execute(delete(WebSession).where(WebSession.expires_at < _now()))
    db.commit()
