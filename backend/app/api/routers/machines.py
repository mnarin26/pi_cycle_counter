from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_panel_8000
from app.config import settings
from app.db.models import Camera, Machine, MachineDowntime
from app.services.audit_log import log_action
from app.services.cycle_export import (
    export_filename,
    machine_cycles_csv,
    machine_summary_csv,
)
from app.services.mold_matcher import MAX_REPLAY_DAYS, replay_mold_history
from app.services.mold_names import clear_orphan_cycle_mold_labels
from app.services.time_windows import RangeKey, resolve_window

router = APIRouter()


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class DowntimeOut(BaseModel):
    id: int
    machine_id: int
    start_at: datetime
    end_at: datetime
    note: str
    created_by: str | None = None

    class Config:
        from_attributes = True


class DowntimeCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    note: str = Field(..., min_length=1, max_length=256)


class MachineOut(BaseModel):
    id: int
    camera_id: int
    name: str
    slot_index: int
    roi_polygon: str
    axis_p0: str
    axis_p1: str
    threshold_mode: str
    threshold_min: int
    threshold_max: int
    threshold_offset: int
    line_thickness: int
    reflector_len_min: int | None
    reflector_len_max: int | None
    occlusion_grace_ms: int
    debounce_ms: int
    stability_confirm_ms: int
    open_position_1d: float
    closed_position_1d: float
    hysteresis: float
    no_movement_timeout_s: float
    current_mold_id: int | None
    diag_from: str | None = None
    diag_until: str | None = None
    enabled: bool

    class Config:
        from_attributes = True


class MachineUpdate(BaseModel):
    camera_id: int | None = None
    name: str | None = None
    roi_polygon: str | None = None
    axis_p0: str | None = None
    axis_p1: str | None = None
    threshold_mode: str | None = None
    threshold_min: int | None = None
    threshold_max: int | None = None
    threshold_offset: int | None = Field(default=None, ge=-120, le=120)
    line_thickness: int | None = Field(default=None, ge=1, le=51)
    reflector_len_min: int | None = Field(default=None, ge=1, le=2000)
    reflector_len_max: int | None = Field(default=None, ge=1, le=2000)
    occlusion_grace_ms: int | None = Field(default=None, ge=0, le=5000)
    debounce_ms: int | None = None
    stability_confirm_ms: int | None = None
    open_position_1d: float | None = None
    closed_position_1d: float | None = None
    hysteresis: float | None = None
    no_movement_timeout_s: float | None = None
    diag_from: str | None = None
    diag_until: str | None = None
    enabled: bool | None = None
    current_mold_id: int | None = None


class ReplayMoldBody(BaseModel):
    range: RangeKey = "daily"
    from_ts: datetime | None = Field(default=None, alias="from")
    to_ts: datetime | None = Field(default=None, alias="to")
    mode: Literal["missing_only", "reprocess"] = "missing_only"

    class Config:
        populate_by_name = True


class ReplayMoldOut(BaseModel):
    machine_id: int
    mode: str
    start: str
    end: str
    cycles_total: int
    cycles_assigned: int
    cycles_skipped_existing: int
    events_created: int


@router.get("", response_model=list[MachineOut])
def list_machines(db: Session = Depends(get_db)):
    return db.query(Machine).order_by(Machine.id).all()


@router.get("/{machine_id}", response_model=MachineOut)
def get_machine(machine_id: int, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    return m


@router.patch("/{machine_id}", response_model=MachineOut)
def update_machine(machine_id: int, body: MachineUpdate, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    if "diag_until" in data:
        raw = data["diag_until"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            data["diag_until"] = None
        else:
            data["diag_until"] = str(raw).strip()
    if "diag_from" in data:
        raw = data["diag_from"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            data["diag_from"] = None
        else:
            data["diag_from"] = str(raw).strip()
    if "diag_until" in data and data["diag_until"] is None:
        data["diag_from"] = None
    if "camera_id" in data and data["camera_id"] is not None:
        cam = db.get(Camera, data["camera_id"])
        if not cam:
            raise HTTPException(400, detail="camera_id not found")
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return m


@router.post("/{machine_id}/roi")
def set_roi(machine_id: int, roi: list[list[float]], db: Session = Depends(get_db)):
    import json

    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    m.roi_polygon = json.dumps(roi)
    db.commit()
    return {"ok": True}


@router.get("/{machine_id}/export")
def export_machine_data(
    machine_id: int,
    kind: Literal["summary", "cycles"] = Query("summary"),
    range: RangeKey = Query("daily"),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    start, end = resolve_window(range, from_ts, to_ts)
    if kind == "summary":
        content = machine_summary_csv(db, machine_id, start, end)
    else:
        content = machine_cycles_csv(db, machine_id, m.name, start, end)
    filename = export_filename(f"makine_{machine_id}", start, end, kind)
    body = "\ufeff" + content
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




@router.get("/{machine_id}/downtimes", response_model=list[DowntimeOut])
def list_downtimes(
    machine_id: int,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    if db.get(Machine, machine_id) is None:
        raise HTTPException(404)
    q = db.query(MachineDowntime).filter(MachineDowntime.machine_id == machine_id)
    if from_ts is not None:
        q = q.filter(MachineDowntime.end_at > _as_utc(from_ts))
    if to_ts is not None:
        q = q.filter(MachineDowntime.start_at < _as_utc(to_ts))
    return q.order_by(MachineDowntime.start_at.desc()).limit(500).all()


@router.post("/{machine_id}/downtimes", response_model=DowntimeOut)
def create_downtime(
    machine_id: int,
    body: DowntimeCreate,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    if db.get(Machine, machine_id) is None:
        raise HTTPException(404)
    start = _as_utc(body.start_at)
    end = _as_utc(body.end_at)
    if end <= start:
        raise HTTPException(400, detail="Duruş bitişi başlangıçtan sonra olmalı")
    if end > datetime.now(timezone.utc):
        raise HTTPException(400, detail="Duruş aralığı gelecekte olamaz")
    dt = MachineDowntime(
        machine_id=machine_id,
        start_at=start,
        end_at=end,
        note=body.note.strip(),
        created_by=user.display_name,
    )
    db.add(dt)
    db.commit()
    db.refresh(dt)
    log_action(
        db,
        actor_type=user.actor_type,
        actor_name=user.display_name,
        telegram_user_id=user.telegram_user_id,
        action="machine.downtime.create",
        resource=f"machine:{machine_id}",
        detail={"start": start.isoformat(), "end": end.isoformat(), "note": dt.note},
    )
    return dt


@router.delete("/{machine_id}/downtimes/{downtime_id}")
def delete_downtime(
    machine_id: int,
    downtime_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    dt = db.get(MachineDowntime, downtime_id)
    if not dt or dt.machine_id != machine_id:
        raise HTTPException(404)
    db.delete(dt)
    db.commit()
    log_action(
        db,
        actor_type=user.actor_type,
        actor_name=user.display_name,
        telegram_user_id=user.telegram_user_id,
        action="machine.downtime.delete",
        resource=f"machine:{machine_id}",
        detail={"downtime_id": downtime_id},
    )
    return {"ok": True}


@router.post("/{machine_id}/replay-mold-matching", response_model=ReplayMoldOut)
def replay_mold_matching(
    machine_id: int,
    body: ReplayMoldBody,
    db: Session = Depends(get_db),
):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    start, end = resolve_window(body.range, body.from_ts, body.to_ts)
    span_days = (end - start).total_seconds() / 86400
    if span_days > MAX_REPLAY_DAYS:
        raise HTTPException(
            400,
            detail=f"En fazla {MAX_REPLAY_DAYS} günlük aralık işlenebilir (seçilen: {span_days:.1f} gün)",
        )
    try:
        if clear_orphan_cycle_mold_labels(db, machine_id):
            db.commit()
        result = replay_mold_history(db, machine_id, start, end, body.mode)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    return result
