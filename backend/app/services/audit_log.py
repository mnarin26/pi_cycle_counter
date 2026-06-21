"""Audit logging: who did what, when."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.models import AuditLog


def log_action(
    db: Session,
    *,
    actor_type: str,
    action: str,
    actor_name: str = "",
    telegram_user_id: str | None = None,
    resource: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Write an audit entry. Never raises (logging must not break the action)."""
    try:
        entry = AuditLog(
            actor_type=actor_type,
            telegram_user_id=telegram_user_id,
            actor_name=actor_name or "",
            action=action,
            resource=resource,
            detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
            ip=ip,
            user_agent=(user_agent or "")[:256] or None,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()


def list_logs(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    rows = db.query(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            detail = json.loads(r.detail_json) if r.detail_json else None
        except json.JSONDecodeError:
            detail = None
        out.append(
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "actor_type": r.actor_type,
                "actor_name": r.actor_name,
                "telegram_user_id": r.telegram_user_id,
                "action": r.action,
                "resource": r.resource,
                "detail": detail,
                "ip": r.ip,
            }
        )
    return out
