from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Cycle, Event

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _resolve_window(
    range_: Literal["daily", "weekly", "monthly", "yearly"],
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = from_ts or _range_start(range_)
    end = to_ts or now
    if end <= start:
        end = start + timedelta(seconds=1)
    return start, end


def _cycle_base_query(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
):
    return db.query(Cycle).filter(
        Cycle.machine_id == machine_id,
        Cycle.t_end >= start,
        Cycle.t_end <= end,
        Cycle.is_counted.is_(True),
    )


def _aggregate_cycle_stats(db: Session, machine_id: int, start: datetime, end: datetime) -> dict:
    row = (
        db.query(
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
            func.min(Cycle.cycle_time_s),
            func.max(Cycle.cycle_time_s),
        )
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .one()
    )
    cnt = int(row[0] or 0)
    if cnt == 0:
        return {
            "cycle_count": 0,
            "avg_cycle_s": 0.0,
            "min_cycle_s": 0.0,
            "max_cycle_s": 0.0,
            "last_cycle_s": 0.0,
        }
    last_s = (
        db.query(Cycle.cycle_time_s)
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .order_by(Cycle.t_end.desc())
        .limit(1)
        .scalar()
    )
    return {
        "cycle_count": cnt,
        "avg_cycle_s": round(float(row[1] or 0), 3),
        "min_cycle_s": round(float(row[2] or 0), 3),
        "max_cycle_s": round(float(row[3] or 0), 3),
        "last_cycle_s": round(float(last_s or 0), 3),
    }


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    range: Literal["daily", "weekly", "monthly", "yearly"] = "daily",
    machine_id: int | None = None,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
):
    start, end = _resolve_window(range, from_ts, to_ts)
    filt = db.query(Cycle).filter(Cycle.t_end >= start, Cycle.t_end <= end, Cycle.is_counted.is_(True))
    if machine_id is not None:
        filt = filt.filter(Cycle.machine_id == machine_id)
    cnt = int(filt.with_entities(func.count(Cycle.id)).scalar() or 0)
    if cnt == 0:
        return {
            "range": range,
            "cycle_count": 0,
            "avg_cycle_s": 0.0,
            "uptime_proxy": 0.0,
            "downtime_proxy": 0.0,
        }
    avg = float(filt.with_entities(func.avg(Cycle.cycle_time_s)).scalar() or 0)
    return {
        "range": range,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "cycle_count": cnt,
        "avg_cycle_s": round(avg, 3),
        "uptime_proxy": round(min(1.0, cnt / max(1, (end - start).total_seconds() / 10)), 3),
        "downtime_proxy": 0.0,
    }


@router.get("/cycles_series")
def cycles_series(
    db: Session = Depends(get_db),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    machine_id: int | None = None,
    limit: int = Query(2500, le=8000),
):
    now = datetime.now(timezone.utc)
    start = from_ts or (now - timedelta(days=1))
    end = to_ts or now
    q = (
        db.query(Cycle)
        .filter(Cycle.t_end >= start, Cycle.t_end <= end, Cycle.is_counted.is_(True))
        .order_by(Cycle.t_end)
    )
    if machine_id is not None:
        q = q.filter(Cycle.machine_id == machine_id)
    rows = q.limit(limit).all()
    if machine_id is not None:
        # #region agent log
        logger.info(
            "[DBG][H1/H4] analytics_cycles_series machine_id=%s limit=%s rows=%s",
            machine_id,
            limit,
            len(rows),
        )
        # #endregion
    return [
        {
            "t": r.t_end.isoformat(),
            "machine_id": r.machine_id,
            "cycle_time_s": r.cycle_time_s,
            "mold_id": r.mold_id,
            "mold": r.mold_name_snapshot,
        }
        for r in rows
    ]


@router.get("/machine_analysis")
def machine_analysis(
    machine_id: int,
    db: Session = Depends(get_db),
    range: Literal["daily", "weekly", "monthly", "yearly"] = "daily",
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    limit: int = 2000,
):
    start, end = _resolve_window(range, from_ts, to_ts)
    rows = (
        db.query(Cycle)
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .order_by(Cycle.t_end)
        .limit(limit)
        .all()
    )
    if not rows:
        return {
            "machine_id": machine_id,
            "range": range,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "summary": {
                "cycle_count": 0,
                "avg_cycle_s": 0.0,
                "min_cycle_s": 0.0,
                "max_cycle_s": 0.0,
                "last_cycle_s": 0.0,
            },
            "mold_breakdown": [],
            "time_buckets": [],
        }

    times = [float(r.cycle_time_s) for r in rows]
    mold_counts: dict[tuple[int | None, str], list[float]] = defaultdict(list)
    for r in rows:
        mold_counts[(r.mold_id, r.mold_name_snapshot or "—")].append(float(r.cycle_time_s))

    bucket_fmt = "%Y-%m-%d %H:00"
    if range == "monthly":
        bucket_fmt = "%Y-%m-%d"
    elif range == "yearly":
        bucket_fmt = "%Y-%m"
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        key = r.t_end.strftime(bucket_fmt)
        buckets[key].append(float(r.cycle_time_s))

    return {
        "machine_id": machine_id,
        "range": range,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "summary": {
            "cycle_count": len(rows),
            "avg_cycle_s": round(sum(times) / len(times), 3),
            "min_cycle_s": round(min(times), 3),
            "max_cycle_s": round(max(times), 3),
            "last_cycle_s": round(times[-1], 3),
        },
        "mold_breakdown": [
            {
                "mold_id": mold_id,
                "mold_name": mold_name,
                "cycle_count": len(vals),
                "share_pct": round(100.0 * len(vals) / len(rows), 2),
                "avg_cycle_s": round(sum(vals) / len(vals), 3),
            }
            for (mold_id, mold_name), vals in sorted(mold_counts.items(), key=lambda kv: len(kv[1]), reverse=True)
        ],
        "time_buckets": [
            {
                "bucket": key,
                "cycle_count": len(vals),
                "avg_cycle_s": round(sum(vals) / len(vals), 3),
            }
            for key, vals in sorted(buckets.items(), key=lambda kv: kv[0])
        ],
    }


@router.get("/machine_dashboard")
def machine_dashboard(
    machine_id: int,
    db: Session = Depends(get_db),
    range: Literal["daily", "weekly", "monthly", "yearly"] = "daily",
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    series_limit: int = Query(4000, ge=100, le=8000),
    events_limit: int = Query(500, ge=10, le=1000),
):
    """Single fast payload for machine detail UI: SQL aggregates + bounded series + events."""
    start, end = _resolve_window(range, from_ts, to_ts)
    stats = _aggregate_cycle_stats(db, machine_id, start, end)
    total = stats["cycle_count"]

    mold_rows = (
        db.query(
            Cycle.mold_id,
            Cycle.mold_name_snapshot,
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
        )
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .group_by(Cycle.mold_id, Cycle.mold_name_snapshot)
        .order_by(func.count(Cycle.id).desc())
        .all()
    )
    mold_breakdown = [
        {
            "mold_id": mid,
            "mold_name": mname or "—",
            "cycle_count": int(cnt),
            "share_pct": round(100.0 * int(cnt) / total, 2) if total else 0.0,
            "avg_cycle_s": round(float(avg or 0), 3),
        }
        for mid, mname, cnt, avg in mold_rows
    ]

    # When truncated, prefer the most recent cycles (detail view cares about "now").
    series_rows = (
        _cycle_base_query(db, machine_id, start, end)
        .order_by(Cycle.t_end.desc())
        .limit(series_limit)
        .all()
    )
    series_rows.reverse()
    series = [
        {
            "t": r.t_end.isoformat(),
            "cycle_time_s": float(r.cycle_time_s),
            "mold_id": r.mold_id,
            "mold": r.mold_name_snapshot,
        }
        for r in series_rows
    ]

    ev_rows = (
        db.query(Event)
        .filter(
            Event.machine_id == machine_id,
            Event.created_at >= start,
            Event.created_at <= end,
        )
        .order_by(Event.created_at)
        .limit(events_limit)
        .all()
    )
    events = [
        {
            "id": e.id,
            "type": e.type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in ev_rows
    ]

    return {
        "machine_id": machine_id,
        "range": range,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "summary": stats,
        "mold_breakdown": mold_breakdown,
        "series": series,
        "series_total": total,
        "series_shown": len(series),
        "series_truncated": total > len(series),
        "events": events,
    }


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
    q = db.query(Cycle.cycle_time_s).filter(Cycle.t_end >= start, Cycle.t_end <= end, Cycle.is_counted.is_(True))
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
