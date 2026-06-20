"""Telegram operator whitelist and permission levels."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.stored_settings import get_telegram_config, normalize_operators


@dataclass
class TelegramOperator:
    id: str
    name: str
    level: int  # 1 = assign + create, 2 = assign only


def _parse_level(value) -> int:
    try:
        lv = int(value)
    except (TypeError, ValueError):
        return 2
    return 1 if lv == 1 else 2


def list_operators(db: Session) -> list[TelegramOperator]:
    raw = get_telegram_config(db)
    ops = raw.get("operators")
    if not isinstance(ops, list):
        ops = normalize_operators(raw)
        return [TelegramOperator(id=o["id"], name=o["name"], level=2) for o in ops]
    result: list[TelegramOperator] = []
    for item in ops:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("id") or item.get("telegram_user_id") or "").strip()
        if not uid:
            continue
        result.append(
            TelegramOperator(
                id=uid,
                name=str(item.get("name") or "").strip() or uid,
                level=_parse_level(item.get("level")),
            )
        )
    return result


def get_operator(db: Session, telegram_user_id: str) -> TelegramOperator | None:
    uid = str(telegram_user_id).strip()
    for op in list_operators(db):
        if op.id == uid:
            return op
    return None


def can_assign(op: TelegramOperator) -> bool:
    return op.level in (1, 2)


def can_create_mold(op: TelegramOperator) -> bool:
    return op.level == 1
