"""Read/write structured values in AppSetting (global JSON blob)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AppSetting

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
                out.append({"id": uid, "name": "", "level": 2})
        return out
    result: list[dict[str, Any]] = []
    for item in ops:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("id") or item.get("telegram_user_id") or "").strip()
        if not uid:
            continue
        try:
            level = int(item.get("level") or 2)
        except (TypeError, ValueError):
            level = 2
        if level not in (1, 2):
            level = 2
        result.append(
            {
                "id": uid,
                "name": str(item.get("name") or "").strip(),
                "level": level,
            }
        )
    return result


def telegram_public_view(raw: dict[str, Any]) -> dict[str, Any]:
    token = raw.get("bot_token")
    masked = mask_token(token if isinstance(token, str) else None)
    operators = normalize_operators(raw)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "bot_username": str(raw.get("bot_username") or "").strip(),
        "operators": operators,
        **masked,
    }


def get_allowed_operator_ids(raw: dict[str, Any]) -> set[str]:
    return {op["id"] for op in normalize_operators(raw)}


def add_operator(
    db: Session,
    *,
    name: str,
    telegram_user_id: str,
    level: int = 2,
) -> dict[str, Any]:
    uid = telegram_user_id.strip()
    if not uid.isdigit():
        raise ValueError("Telegram user ID sadece rakamlardan olusmalidir")
    nm = name.strip()
    if not nm:
        raise ValueError("Operatör adi bos olamaz")
    if level not in (1, 2):
        raise ValueError("Seviye 1 veya 2 olmali")
    raw = get_section(db, "telegram")
    ops = normalize_operators(raw)
    ops = [o for o in ops if o["id"] != uid]
    ops.append({"id": uid, "name": nm, "level": level})
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
