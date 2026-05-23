from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Event, Cycle, Machine, Mold, MoldMachine

router = APIRouter()


def _range_start(range_: str) -> datetime:
    now = datetime.now(timezone.utc)
    if range_ == "daily":
        return now - timedelta(days=1)
    if range_ == "weekly":
        return now - timedelta(days=7)
    if range_ == "monthly":
        return now - timedelta(days=31)
    if range_ == "yearly":
        return now - timedelta(days=365)
    return now - timedelta(days=31)


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


@router.get("/usage")
def mold_usage(
    db: Session = Depends(get_db),
    range: str = "monthly",
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
):
    start = from_ts or _range_start(range)
    end = to_ts or datetime.now(timezone.utc)
    if end <= start:
        end = start + timedelta(seconds=1)

    machine_names = {m.id: m.name for m in db.query(Machine).all()}
    molds = {m.id: m for m in db.query(Mold).all()}
    rows = (
        db.query(Cycle)
        .filter(
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.mold_id.is_not(None),
            Cycle.is_counted.is_(True),
        )
        .all()
    )
    grouped: dict[int, dict] = defaultdict(lambda: {"total": 0, "machines": defaultdict(int), "times": []})
    for c in rows:
        if c.mold_id is None:
            continue
        grouped[c.mold_id]["total"] += 1
        grouped[c.mold_id]["machines"][c.machine_id] += 1
        grouped[c.mold_id]["times"].append(float(c.cycle_time_s))

    out = []
    for mold_id, agg in sorted(grouped.items(), key=lambda kv: kv[1]["total"], reverse=True):
        mold = molds.get(mold_id)
        times = agg["times"]
        out.append(
            {
                "mold_id": mold_id,
                "mold_name": (mold.name if mold else None) or "İsimsiz kalıp",
                "status": mold.status if mold else "unknown",
                "total_cycles": agg["total"],
                "avg_cycle_s": round(sum(times) / len(times), 3) if times else 0.0,
                "machines": [
                    {
                        "machine_id": mid,
                        "machine_name": machine_names.get(mid, f"Makine #{mid}"),
                        "cycle_count": cnt,
                    }
                    for mid, cnt in sorted(agg["machines"].items(), key=lambda kv: kv[1], reverse=True)
                ],
            }
        )
    return {
        "range": range,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": out,
    }


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
