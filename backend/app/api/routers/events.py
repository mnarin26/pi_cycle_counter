from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Event

router = APIRouter()


@router.get("")
def list_events(
    db: Session = Depends(get_db),
    machine_id: int | None = None,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    limit: int = 200,
):
    q = db.query(Event).order_by(Event.created_at.desc())
    if machine_id is not None:
        q = q.filter(Event.machine_id == machine_id)
    if from_ts:
        q = q.filter(Event.created_at >= from_ts)
    if to_ts:
        q = q.filter(Event.created_at <= to_ts)
    rows = q.limit(limit).all()
    return [
        {
            "id": e.id,
            "type": e.type,
            "machine_id": e.machine_id,
            "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]
