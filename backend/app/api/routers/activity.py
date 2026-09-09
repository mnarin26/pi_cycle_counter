from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_db
from app.services.activity import CLIENT_ACTIONS, list_activity
from app.services.audit_log import log_action

router = APIRouter()


@router.get("")
def get_activity(db: Session = Depends(get_db), limit: int = 300):
    """User-facing activity feed (mold changes, TV selection, detail views)."""
    return list_activity(db, limit=limit)


class ActivityIn(BaseModel):
    action: Literal["tv.machines.select", "machine.detail.view"]
    machine_id: int | None = None
    machine_name: str | None = None


@router.post("")
def post_activity(
    body: ActivityIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if body.action not in CLIENT_ACTIONS:
        raise HTTPException(status_code=400, detail="Gecersiz islem")
    detail: dict[str, Any] = {}
    if body.machine_id is not None:
        detail["machine_id"] = body.machine_id
    if body.machine_name:
        detail["machine_name"] = body.machine_name
    log_action(
        db,
        actor_type=user.actor_type,
        actor_name=user.display_name,
        telegram_user_id=user.telegram_user_id,
        action=body.action,
        detail=detail or None,
        ip=client_ip(request),
    )
    return {"ok": True}
