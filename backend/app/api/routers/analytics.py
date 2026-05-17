from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Cycle

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
    return now - timedelta(days=1)


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    range: Literal["daily", "weekly", "monthly", "yearly"] = "daily",
    machine_id: int | None = None,
):
    start = _range_start(range)
    q = db.query(Cycle).filter(Cycle.t_end >= start)
    if machine_id is not None:
        q = q.filter(Cycle.machine_id == machine_id)
    rows = q.all()
    if not rows:
        return {
            "range": range,
            "cycle_count": 0,
            "avg_cycle_s": 0.0,
            "uptime_proxy": 0.0,
            "downtime_proxy": 0.0,
        }
    times = [r.cycle_time_s for r in rows]
    avg = sum(times) / len(times)
    return {
        "range": range,
        "cycle_count": len(rows),
        "avg_cycle_s": round(avg, 3),
        "uptime_proxy": round(min(1.0, len(rows) / max(1, (datetime.now(timezone.utc) - start).total_seconds() / 10)), 3),
        "downtime_proxy": 0.0,
    }


@router.get("/cycles_series")
def cycles_series(
    db: Session = Depends(get_db),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    machine_id: int | None = None,
    limit: int = 500,
):
    now = datetime.now(timezone.utc)
    start = from_ts or (now - timedelta(days=1))
    end = to_ts or now
    q = db.query(Cycle).filter(Cycle.t_end >= start, Cycle.t_end <= end).order_by(Cycle.t_end)
    if machine_id is not None:
        q = q.filter(Cycle.machine_id == machine_id)
    rows = q.limit(limit).all()
    return [
        {
            "t": r.t_end.isoformat(),
            "machine_id": r.machine_id,
            "cycle_time_s": r.cycle_time_s,
            "mold": r.mold_name_snapshot,
        }
        for r in rows
    ]


@router.get("/histogram")
def histogram(
    db: Session = Depends(get_db),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    machine_id: int | None = None,
    bins: int = 20,
):
    now = datetime.now(timezone.utc)
    start = from_ts or (now - timedelta(days=7))
    end = to_ts or now
    q = db.query(Cycle.cycle_time_s).filter(Cycle.t_end >= start, Cycle.t_end <= end)
    if machine_id is not None:
        q = q.filter(Cycle.machine_id == machine_id)
    vals = [float(r[0]) for r in q.all() if r[0] is not None]
    if not vals:
        return {"bins": [], "counts": []}
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi = lo + 1e-3
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        i = int((v - lo) / width)
        i = max(0, min(bins - 1, i))
        counts[i] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return {"bin_edges": edges, "counts": counts}
