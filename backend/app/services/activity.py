"""User-facing activity feed derived from the audit log.

Only actions that a machine operator cares about are surfaced here (mold
create/assign/update/delete, TV machine selection, machine detail views).
Technical/vision events and auth/settings records stay out of this feed; they
remain in their own tables for the 8080 panel.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.models import AuditLog

# Whitelist of audit actions shown to users, newest first.
ACTIVITY_ACTIONS = {
    "mold.create",
    "mold.assign",
    "mold.update",
    "mold.delete",
    "tv.machines.select",
    "machine.detail.view",
}

# Actions the panel is allowed to record via POST /api/activity.
CLIENT_ACTIONS = {"tv.machines.select", "machine.detail.view"}


def _machine_label(detail: dict[str, Any]) -> str:
    name = detail.get("machine_name") or detail.get("machine")
    if name:
        return str(name)
    mid = detail.get("machine_id")
    return f"#{mid}" if mid is not None else "bilinmeyen makine"


def _mold_label(detail: dict[str, Any]) -> str:
    name = detail.get("mold_name") or detail.get("mold") or detail.get("name")
    qr = detail.get("mold_qr_code") or detail.get("qr_code")
    if name and qr and str(qr) not in str(name):
        return f"{name} ({qr})"
    if name:
        return str(name)
    if qr:
        return f"kod {qr}"
    mid = detail.get("mold_id")
    return f"#{mid}" if mid is not None else "—"


def describe(action: str, actor_name: str, detail: dict[str, Any] | None) -> str:
    d = detail or {}
    who = actor_name or "Bir kullanıcı"
    if action == "mold.create":
        return f"{who} kalıp oluşturdu: {_mold_label(d)}"
    if action == "mold.assign":
        return f"{who}, {_mold_label(d)} kalıbını {_machine_label(d)} makinesine atadı"
    if action == "mold.update":
        return f"{who} kalıbı güncelledi: {_mold_label(d)}"
    if action == "mold.delete":
        return f"{who} kalıbı sildi: {_mold_label(d)}"
    if action == "tv.machines.select":
        return f"{who} bilgi ekranı makine seçimini değiştirdi"
    if action == "machine.detail.view":
        return f"{who} {_machine_label(d)} detaylarını inceledi"
    return f"{who}: {action}"


def list_activity(db: Session, *, limit: int = 300) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(ACTIVITY_ACTIONS))
        .order_by(desc(AuditLog.id))
        .limit(limit)
        .all()
    )
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
                "actor_name": r.actor_name,
                "action": r.action,
                "text": describe(r.action, r.actor_name, detail),
            }
        )
    return out
