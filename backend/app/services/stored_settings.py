"""Read/write structured values in AppSetting (global JSON blob)."""

from __future__ import annotations

import hmac
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AppSetting
from app.services.daily_password import hash_password

MIN_OPERATOR_PASSWORD_LEN = 4
MAX_OPERATOR_PASSWORD_LEN = 64

GLOBAL_KEY = "global"


def _load_global(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, GLOBAL_KEY)
    if not row:
        return {}
    try:
        data = json.loads(row.value_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_global(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    row = db.get(AppSetting, GLOBAL_KEY)
    if not row:
        row = AppSetting(key=GLOBAL_KEY, value_json="{}")
        db.add(row)
        db.flush()
    row.value_json = json.dumps(data, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return data


def get_section(db: Session, section: str) -> dict[str, Any]:
    global_data = _load_global(db)
    block = global_data.get(section)
    return dict(block) if isinstance(block, dict) else {}


def patch_section(db: Session, section: str, patch: dict[str, Any]) -> dict[str, Any]:
    global_data = _load_global(db)
    current = global_data.get(section)
    if not isinstance(current, dict):
        current = {}
    current.update(patch)
    global_data[section] = current
    _save_global(db, global_data)
    return current


def mask_token(token: str | None) -> dict[str, Any]:
    if not token or not str(token).strip():
        return {"token_set": False, "token_hint": None}
    t = str(token).strip()
    hint = t[-4:] if len(t) >= 4 else "****"
    return {"token_set": True, "token_hint": hint}


PERMISSION_KEYS = ("panel_8000", "panel_8080", "bot_mold_create", "bot_mold_assign")


def _coerce_perms(role: str, raw_perms: Any) -> dict[str, bool]:
    if role == "admin":
        return {k: True for k in PERMISSION_KEYS}
    perms = raw_perms if isinstance(raw_perms, dict) else {}
    return {k: bool(perms.get(k, False)) for k in PERMISSION_KEYS}


def _migrate_operator(item: dict[str, Any]) -> dict[str, Any] | None:
    telegram_id = str(item.get("telegram_user_id") or "").strip()
    uid = str(item.get("id") or "").strip()
    if not uid and telegram_id:
        uid = telegram_id
    if uid.isdigit() and not telegram_id:
        telegram_id = uid
    if not uid:
        return None
    name = str(item.get("name") or "").strip()
    role = str(item.get("role") or "").strip().lower()
    if role not in ("admin", "user"):
        # Legacy level-based migration: level 1 -> admin, level 2 -> user (assign only)
        try:
            level = int(item.get("level") or 2)
        except (TypeError, ValueError):
            level = 2
        if level == 1:
            role = "admin"
            raw_perms: Any = None
        else:
            role = "user"
            raw_perms = {"bot_mold_assign": True}
    else:
        raw_perms = item.get("permissions")
    pw_hash = str(item.get("password_hash") or "").strip()
    return {
        "id": uid,
        "telegram_user_id": telegram_id,
        "name": name,
        "role": role,
        "permissions": _coerce_perms(role, raw_perms),
        "password_hash": pw_hash,
    }


def _new_local_id(ops: list[dict[str, Any]]) -> str:
    existing = {o["id"] for o in ops}
    n = 1
    while f"local_{n}" in existing:
        n += 1
    return f"local_{n}"


def _name_key(name: str | None) -> str:
    return " ".join((name or "").strip().split()).casefold()


def _name_taken(ops: list[dict[str, Any]], name: str, *, except_id: str | None = None) -> bool:
    key = _name_key(name)
    if not key:
        return False
    for op in ops:
        if except_id and op["id"] == except_id:
            continue
        if _name_key(op.get("name")) == key:
            return True
    return False


def find_operator_by_name(db: Session, name: str) -> dict[str, Any] | None:
    key = _name_key(name)
    if not key:
        return None
    hits = [op for op in normalize_operators(get_section(db, "telegram")) if _name_key(op.get("name")) == key]
    return hits[0] if hits else None


def operator_password_matches(rec: dict[str, Any], password: str) -> bool:
    stored = str(rec.get("password_hash") or "")
    if not stored or not (password or "").strip():
        return False
    return hmac.compare_digest(stored, hash_password(password.strip()))


def _normalize_telegram_id(value: str | None) -> str:
    tg = str(value or "").strip()
    if not tg:
        return ""
    if not tg.isdigit():
        raise ValueError("Telegram user ID sadece rakamlardan olusmalidir")
    return tg


def _normalize_password(password: str | None, *, required: bool) -> str:
    pw = (password or "").strip()
    if not pw:
        if required:
            raise ValueError("Sabit sifre gerekli")
        return ""
    if len(pw) < MIN_OPERATOR_PASSWORD_LEN:
        raise ValueError(f"Sabit sifre en az {MIN_OPERATOR_PASSWORD_LEN} karakter olmali")
    if len(pw) > MAX_OPERATOR_PASSWORD_LEN:
        raise ValueError(f"Sabit sifre en fazla {MAX_OPERATOR_PASSWORD_LEN} karakter olmali")
    return pw


def _password_taken(ops: list[dict[str, Any]], password_hash: str, *, except_id: str | None = None) -> bool:
    for op in ops:
        if except_id and op["id"] == except_id:
            continue
        stored = str(op.get("password_hash") or "")
        if stored and hmac.compare_digest(stored, password_hash):
            return True
    return False


def _telegram_taken(ops: list[dict[str, Any]], telegram_id: str, *, except_id: str | None = None) -> bool:
    if not telegram_id:
        return False
    for op in ops:
        if except_id and op["id"] == except_id:
            continue
        if str(op.get("telegram_user_id") or "") == telegram_id or op["id"] == telegram_id:
            return True
    return False


def operator_public_view(op: dict[str, Any]) -> dict[str, Any]:
    tg = str(op.get("telegram_user_id") or "").strip()
    has_password = bool(str(op.get("password_hash") or "").strip())
    return {
        "id": op["id"],
        "name": op.get("name") or "",
        "role": op.get("role") or "user",
        "permissions": op.get("permissions") or {},
        "telegram_user_id": tg,
        "has_telegram": bool(tg),
        "has_password": has_password,
    }


def normalize_operators(raw: dict[str, Any]) -> list[dict[str, Any]]:
    ops = raw.get("operators")
    if not isinstance(ops, list):
        legacy = raw.get("allowed_user_ids") or raw.get("allowed_users") or ""
        if isinstance(legacy, list):
            legacy = ",".join(str(x) for x in legacy)
        out: list[dict[str, Any]] = []
        for part in str(legacy).split(","):
            uid = part.strip()
            if uid.isdigit():
                out.append(
                    {
                        "id": uid,
                        "telegram_user_id": uid,
                        "name": "",
                        "role": "user",
                        "permissions": _coerce_perms("user", {"bot_mold_assign": True}),
                        "password_hash": "",
                    }
                )
        return out
    result: list[dict[str, Any]] = []
    for item in ops:
        if not isinstance(item, dict):
            continue
        migrated = _migrate_operator(item)
        if migrated:
            result.append(migrated)
    return result


def telegram_public_view(raw: dict[str, Any]) -> dict[str, Any]:
    token = raw.get("bot_token")
    masked = mask_token(token if isinstance(token, str) else None)
    operators = [operator_public_view(op) for op in normalize_operators(raw)]
    return {
        "enabled": bool(raw.get("enabled", False)),
        "bot_username": str(raw.get("bot_username") or "").strip(),
        "operators": operators,
        **masked,
    }


def get_allowed_operator_ids(raw: dict[str, Any]) -> set[str]:
    return {op["id"] for op in normalize_operators(raw)}


def get_operator_record(db: Session, operator_id: str) -> dict[str, Any] | None:
    uid = str(operator_id).strip()
    raw = get_section(db, "telegram")
    for op in normalize_operators(raw):
        if op["id"] == uid or str(op.get("telegram_user_id") or "") == uid:
            return op
    return None


def match_operator_password(db: Session, password: str) -> dict[str, Any] | None:
    pw = (password or "").strip()
    if not pw:
        return None
    target = hash_password(pw)
    for op in normalize_operators(get_section(db, "telegram")):
        stored = str(op.get("password_hash") or "")
        if stored and hmac.compare_digest(stored, target):
            return op
    return None


def add_operator(
    db: Session,
    *,
    name: str,
    telegram_user_id: str | None = None,
    role: str = "user",
    permissions: dict[str, Any] | None = None,
    password: str | None = None,
    level: int | None = None,
) -> dict[str, Any]:
    nm = name.strip()
    if not nm:
        raise ValueError("Operatör adi bos olamaz")
    if level is not None and (role or "user") == "user":
        role = "admin" if int(level) == 1 else "user"
    role = (role or "user").strip().lower()
    if role not in ("admin", "user"):
        raise ValueError("Rol 'admin' veya 'user' olmali")
    tg = _normalize_telegram_id(telegram_user_id)
    pw = _normalize_password(password, required=False)
    if not tg and not pw:
        raise ValueError("Telegram ID veya sabit sifre gerekli")
    raw = get_section(db, "telegram")
    ops = normalize_operators(raw)
    if _name_taken(ops, nm):
        raise ValueError("Bu isim zaten kayitli")
    if tg and _telegram_taken(ops, tg):
        raise ValueError("Bu Telegram ID zaten kayitli")
    pw_hash = hash_password(pw) if pw else ""
    if pw_hash and _password_taken(ops, pw_hash):
        raise ValueError("Bu sabit sifre baska bir kullanicida kayitli")
    uid = tg if tg else _new_local_id(ops)
    ops = [o for o in ops if o["id"] != uid]
    ops.append(
        {
            "id": uid,
            "telegram_user_id": tg,
            "name": nm,
            "role": role,
            "permissions": _coerce_perms(role, permissions),
            "password_hash": pw_hash,
        }
    )
    patch_section(db, "telegram", {"operators": ops})
    return telegram_public_view(get_section(db, "telegram"))


def update_operator(
    db: Session,
    *,
    telegram_user_id: str,
    name: str | None = None,
    role: str | None = None,
    permissions: dict[str, Any] | None = None,
    password: str | None = None,
    new_telegram_user_id: str | None = None,
) -> dict[str, Any]:
    uid = telegram_user_id.strip()
    raw = get_section(db, "telegram")
    ops = normalize_operators(raw)
    found = next((o for o in ops if o["id"] == uid), None)
    if not found:
        raise ValueError("Operatör bulunamadi")
    if name is not None:
        nm = name.strip()
        if not nm:
            raise ValueError("Operatör adi bos olamaz")
        if _name_taken(ops, nm, except_id=uid):
            raise ValueError("Bu isim zaten kayitli")
        found["name"] = nm
    if role is not None:
        r = role.strip().lower()
        if r not in ("admin", "user"):
            raise ValueError("Rol 'admin' veya 'user' olmali")
        found["role"] = r
    if permissions is not None or role is not None:
        found["permissions"] = _coerce_perms(
            found["role"], permissions if permissions is not None else found.get("permissions")
        )
    if new_telegram_user_id is not None:
        tg = _normalize_telegram_id(new_telegram_user_id)
        if tg and _telegram_taken(ops, tg, except_id=uid):
            raise ValueError("Bu Telegram ID zaten kayitli")
        found["telegram_user_id"] = tg
    if password is not None and str(password).strip():
        pw = _normalize_password(password, required=True)
        pw_hash = hash_password(pw)
        if _password_taken(ops, pw_hash, except_id=uid):
            raise ValueError("Bu sabit sifre baska bir kullanicida kayitli")
        found["password_hash"] = pw_hash
    if not str(found.get("telegram_user_id") or "").strip() and not str(found.get("password_hash") or "").strip():
        raise ValueError("Telegram ID veya sabit sifre gerekli")
    patch_section(db, "telegram", {"operators": ops})
    return telegram_public_view(get_section(db, "telegram"))


def remove_operator(db: Session, user_id: str) -> dict[str, Any]:
    uid = user_id.strip()
    raw = get_section(db, "telegram")
    ops = [o for o in normalize_operators(raw) if o["id"] != uid]
    patch_section(db, "telegram", {"operators": ops})
    return telegram_public_view(get_section(db, "telegram"))


def ssh_public_view(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": str(raw.get("host") or "").strip(),
        "user": str(raw.get("user") or "pi").strip(),
        "port": int(raw.get("port") or 22),
        "auth_method": raw.get("auth_method") if raw.get("auth_method") in ("key", "password") else "key",
        "key_path": str(raw.get("key_path") or "~/.ssh/id_ed25519").strip(),
        "alias": str(raw.get("alias") or "").strip(),
    }


def get_telegram_config(db: Session) -> dict[str, Any]:
    """Full telegram config including token (server-side only)."""
    return get_section(db, "telegram")


def ssh_connection_string(raw: dict[str, Any]) -> str:
    user = str(raw.get("user") or "pi").strip()
    host = str(raw.get("host") or "").strip()
    port = int(raw.get("port") or 22)
    if not host:
        return ""
    if port == 22:
        return f"ssh {user}@{host}"
    return f"ssh -p {port} {user}@{host}"
