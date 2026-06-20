from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Cycle, Machine, Mold, MoldMachine
from app.services.cycle_export import (
    export_filename,
    mold_cycles_csv,
    molds_summary_csv,
)
from app.services.mold_names import (
    backfill_mold_name_snapshots,
    clear_mold_name_snapshots,
    clear_orphan_cycle_mold_labels,
)
from app.services.time_windows import resolve_window

router = APIRouter()


class MoldOut(BaseModel):
    id: int
    name: str | None
    qr_code: str | None = None
    status: str
    avg_cycle_s: float
    tolerance_s: float
    stdev_limit_s: float | None = None
    sample_count: int
    confidence: float
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class NameBody(BaseModel):
    name: str


class MoldUpdate(BaseModel):
    name: str | None = None
    qr_code: str | None = Field(default=None, max_length=64)
    status: Literal["candidate", "active", "ignored"] | None = None
    avg_cycle_s: float | None = Field(default=None, gt=0)
    tolerance_s: float | None = Field(default=None, gt=0)
    stdev_limit_s: float | None = Field(default=None, gt=0)


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
    start, end = resolve_window(range, from_ts, to_ts)  # type: ignore[arg-type]

    machine_names = {m.id: m.name for m in db.query(Machine.id, Machine.name).all()}
    molds = {m.id: m for m in db.query(Mold).all()}

    agg_rows = (
        db.query(
            Cycle.mold_id,
            Cycle.machine_id,
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
        )
        .filter(
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.mold_id.is_not(None),
            Cycle.is_counted.is_(True),
        )
        .group_by(Cycle.mold_id, Cycle.machine_id)
        .all()
    )

    grouped: dict[int, dict] = defaultdict(lambda: {"total": 0, "machines": {}, "time_sum": 0.0})
    for mold_id, machine_id, cnt, avg_s in agg_rows:
        if mold_id is None:
            continue
        c = int(cnt)
        grouped[mold_id]["total"] += c
        grouped[mold_id]["machines"][int(machine_id)] = c
        grouped[mold_id]["time_sum"] += float(avg_s or 0) * c

    out = []
    for mold_id, agg in sorted(grouped.items(), key=lambda kv: kv[1]["total"], reverse=True):
        mold = molds.get(mold_id)
        total = agg["total"]
        avg_cycle = agg["time_sum"] / total if total else 0.0
        out.append(
            {
                "mold_id": mold_id,
                "mold_name": (mold.name if mold else None) or "İsimsiz kalıp",
                "status": mold.status if mold else "unknown",
                "total_cycles": total,
                "avg_cycle_s": round(avg_cycle, 3),
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


@router.get("/export")
def export_molds_data(
    mold_id: int = Query(..., description="Kalıp kimliği (zorunlu)"),
    kind: Literal["summary", "cycles"] = Query("summary"),
    range: str = Query("monthly"),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    if db.get(Mold, mold_id) is None:
        raise HTTPException(404, detail="Kalıp bulunamadı")
    start, end = resolve_window(range, from_ts, to_ts)  # type: ignore[arg-type]
    if kind == "summary":
        content = molds_summary_csv(db, start, end, mold_id=mold_id)
    else:
        content = mold_cycles_csv(db, mold_id, start, end)
    filename = export_filename(f"kalip_{mold_id}", start, end, kind)
    body = "\ufeff" + content
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{mold_id}", response_model=MoldOut)
def update_mold(mold_id: int, body: MoldUpdate, db: Session = Depends(get_db)):
    m = db.get(Mold, mold_id)
    if not m:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    if "stdev_limit_s" in data and data["stdev_limit_s"] is None:
        data["stdev_limit_s"] = None
    if "name" in data:
        name = (data["name"] or "").strip()
        data["name"] = name or None
        if name and data.get("status") is None and m.status == "candidate":
            m.status = "active"
    if "qr_code" in data:
        qc = (data["qr_code"] or "").strip() or None
        if qc:
            clash = db.query(Mold).filter(Mold.qr_code == qc, Mold.id != mold_id).first()
            if clash:
                raise HTTPException(400, detail="Bu QR kodu baska kalıpta kullanılıyor")
        data["qr_code"] = qc
    for k, v in data.items():
        setattr(m, k, v)
    if m.name:
        backfill_mold_name_snapshots(db, mold_id, m.name)
    elif "name" in data and not m.name:
        clear_mold_name_snapshots(db, mold_id)
        clear_orphan_cycle_mold_labels(db)
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{mold_id}")
def delete_mold(mold_id: int, db: Session = Depends(get_db)):
    m = db.get(Mold, mold_id)
    if not m:
        raise HTTPException(404)
    db.query(Cycle).filter(Cycle.mold_id == mold_id).update(
        {Cycle.mold_id: None, Cycle.mold_name_snapshot: None},
        synchronize_session=False,
    )
    db.query(Machine).filter(Machine.current_mold_id == mold_id).update(
        {Machine.current_mold_id: None},
        synchronize_session=False,
    )
    db.query(MoldMachine).filter(MoldMachine.mold_id == mold_id).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.post("/{mold_id}/name", response_model=MoldOut)
def name_mold(mold_id: int, body: NameBody, db: Session = Depends(get_db)):
    m = db.get(Mold, mold_id)
    if not m:
        raise HTTPException(404)
    m.name = body.name
    m.status = "active"
    backfill_mold_name_snapshots(db, mold_id, body.name)
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
