"""Telegram operator whitelist and permission flags."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services.stored_settings import PERMISSION_KEYS, normalize_operators, get_telegram_config


@dataclass
class TelegramOperator:
    id: str
    name: str
    role: str  # admin, user
    permissions: dict[str, bool] = field(default_factory=dict)

    def has(self, perm: str) -> bool:
        return bool(self.permissions.get(perm, False))


def _to_operator(item: dict) -> TelegramOperator:
    perms = item.get("permissions") or {}
    return TelegramOperator(
        id=item["id"],
        name=item.get("name") or item["id"],
        role=item.get("role") or "user",
        permissions={k: bool(perms.get(k, False)) for k in PERMISSION_KEYS},
    )


def list_operators(db: Session) -> list[TelegramOperator]:
    raw = get_telegram_config(db)
    return [_to_operator(o) for o in normalize_operators(raw)]


def get_operator(db: Session, telegram_user_id: str) -> TelegramOperator | None:
    uid = str(telegram_user_id).strip()
    for op in list_operators(db):
        if op.id == uid:
            return op
    return None


def can_assign(op: TelegramOperator) -> bool:
    return op.has("bot_mold_assign")


def can_create_mold(op: TelegramOperator) -> bool:
    return op.has("bot_mold_create")
