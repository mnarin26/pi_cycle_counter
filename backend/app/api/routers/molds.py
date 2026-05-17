from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Event, Mold, MoldMachine

router = APIRouter()


class MoldOut(BaseModel):
    id: int
    name: str | None
    status: str
    avg_cycle_s: float
    tolerance_s: float
    sample_count: int
    confidence: float

    class Config:
        from_attributes = True


class NameBody(BaseModel):
    name: str


class ConfirmMatchBody(BaseModel):
    use_existing_mold_id: int
    update_average: bool = True
    sample_cycle_s: float | None = None


@router.get("", response_model=list[MoldOut])
def list_molds(db: Session = Depends(get_db)):
    return db.query(Mold).order_by(Mold.id.desc()).limit(200).all()


@router.post("/{mold_id}/name", response_model=MoldOut)
def name_mold(mold_id: int, body: NameBody, db: Session = Depends(get_db)):
    m = db.get(Mold, mold_id)
    if not m:
        raise HTTPException(404)
    m.name = body.name
    m.status = "active"
    db.commit()
    db.refresh(m)
    return m


@router.post("/{mold_id}/ignore", response_model=MoldOut)
def ignore_mold(mold_id: int, db: Session = Depends(get_db)):
    m = db.get(Mold, mold_id)
    if not m:
        raise HTTPException(404)
    m.status = "ignored"
    db.commit()
    db.refresh(m)
    return m


@router.post("/{mold_id}/confirm-match")
def confirm_match(mold_id: int, body: ConfirmMatchBody, db: Session = Depends(get_db)):
    """Attach machine context from payload or require machine_id query — simplified: mold stats update."""
    target = db.get(Mold, body.use_existing_mold_id)
    if not target:
        raise HTTPException(404)
    old = db.get(Mold, mold_id)
    if old and old.id != target.id:
        for link in db.query(MoldMachine).filter_by(mold_id=old.id).all():
            link.mold_id = target.id
        db.delete(old)
    if body.update_average and body.sample_cycle_s is not None:
        n = max(1, target.sample_count)
        target.avg_cycle_s = (n * target.avg_cycle_s + body.sample_cycle_s) / (n + 1)
        target.sample_count = n + 1
    db.commit()
    return {"ok": True}
