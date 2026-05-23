from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Machine

router = APIRouter()


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
    enabled: bool

    class Config:
        from_attributes = True


class MachineUpdate(BaseModel):
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
    enabled: bool | None = None
    current_mold_id: int | None = None


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
    for k, v in body.model_dump(exclude_unset=True).items():
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
