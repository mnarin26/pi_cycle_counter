from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Cycle, Event, Machine, Mold
from app.services.mold_names import (
    enrich_series_mold_names,
    mold_name_map,
    resolve_cycle_mold_label,
    resolve_mold_display_name,
)
from app.services.time_windows import format_window_label, resolve_window

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _trend_bucket_sql_fmt(range_: str, start: datetime, end: datetime) -> tuple[str, str]:
    """Pick SQLite strftime bucket; second value is UI resolution hint."""
    span_s = (end - start).total_seconds()
    if range_ == "yearly" or span_s > 120 * 86400:
        return "%Y-%m", "month"
    if range_ == "monthly" or span_s > 21 * 86400:
        return "%Y-%m-%d", "day"
    return "%Y-%m-%d %H:00", "hour"


def _bucket_label_to_ms(bucket: str, resolution: str) -> int:
    if resolution == "month":
        dt = datetime.strptime(bucket, "%Y-%m").replace(tzinfo=timezone.utc)
    elif resolution == "day":
        dt = datetime.strptime(bucket, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.strptime(bucket, "%Y-%m-%d %H:00").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _fetch_trend_buckets(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
    range_: str,
) -> tuple[str, list[dict], list[dict]]:
    """One grouped SQL pass: daily/hourly buckets + per-mold slices + global mold breakdown."""
    fmt, resolution = _trend_bucket_sql_fmt(range_, start, end)
    bucket_col = func.strftime(fmt, Cycle.t_end).label("bucket")
    base = (
        Cycle.machine_id == machine_id,
        Cycle.t_end >= start,
        Cycle.t_end <= end,
        Cycle.is_counted.is_(True),
    )

    rows = (
        db.query(
            bucket_col,
            Cycle.mold_id,
            Cycle.mold_name_snapshot,
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
            func.min(Cycle.cycle_time_s),
            func.max(Cycle.cycle_time_s),
        )
        .filter(*base)
        .group_by(bucket_col, Cycle.mold_id, Cycle.mold_name_snapshot)
        .order_by(bucket_col)
        .all()
    )

    mold_by_bucket: dict[str, list[dict]] = defaultdict(list)
    bucket_acc: dict[str, dict] = {}
    mold_global: dict[tuple[int | None, str], dict] = {}

    for bucket, mold_id, mname, cnt, avg, mn, mx in rows:
        if not bucket:
            continue
        b = str(bucket)
        name = resolve_cycle_mold_label(db, mold_id, mname) or "—"
        n = int(cnt)
        avg_f = float(avg or 0)
        mold_by_bucket[b].append({"mold_name": name, "count": n})

        acc = bucket_acc.setdefault(
            b,
            {"cycle_count": 0, "sum_time": 0.0, "min_cycle_s": float("inf"), "max_cycle_s": 0.0},
        )
        acc["cycle_count"] += n
        acc["sum_time"] += n * avg_f
        acc["min_cycle_s"] = min(acc["min_cycle_s"], float(mn or 0))
        acc["max_cycle_s"] = max(acc["max_cycle_s"], float(mx or 0))

        gkey = (mold_id, name)
        g = mold_global.setdefault(gkey, {"count": 0, "sum_time": 0.0})
        g["count"] += n
        g["sum_time"] += n * avg_f

    out: list[dict] = []
    for b in sorted(bucket_acc.keys()):
        acc = bucket_acc[b]
        cnt = acc["cycle_count"]
        out.append(
            {
                "bucket": b,
                "t_ms": _bucket_label_to_ms(b, resolution),
                "cycle_count": cnt,
                "avg_cycle_s": round(acc["sum_time"] / cnt, 3) if cnt else 0.0,
                "min_cycle_s": round(acc["min_cycle_s"], 3) if cnt else 0.0,
                "max_cycle_s": round(acc["max_cycle_s"], 3),
                "by_mold": sorted(mold_by_bucket.get(b, []), key=lambda x: x["count"], reverse=True),
            }
        )

    total_cycles = sum(a["cycle_count"] for a in bucket_acc.values())
    mold_breakdown = [
        {
            "mold_id": mid,
            "mold_name": mname,
            "cycle_count": g["count"],
            "share_pct": round(100.0 * g["count"] / total_cycles, 2) if total_cycles else 0.0,
            "avg_cycle_s": round(g["sum_time"] / g["count"], 3) if g["count"] else 0.0,
        }
        for (mid, mname), g in sorted(mold_global.items(), key=lambda kv: kv[1]["count"], reverse=True)
    ]
    return resolution, out, mold_breakdown


def _iso_utc_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _cycle_series_row_dict(r) -> dict:
    t_end = r.t_end if hasattr(r, "t_end") else r[0]
    cycle_time_s = r.cycle_time_s if hasattr(r, "cycle_time_s") else r[1]
    mold_id = r.mold_id if hasattr(r, "mold_id") else r[2]
    mold_name = r.mold_name_snapshot if hasattr(r, "mold_name_snapshot") else r[3]
    iso = _iso_utc_z(t_end) if hasattr(t_end, "isoformat") else str(t_end)
    return {
        "t": iso,
        "cycle_time_s": float(cycle_time_s),
        "mold_id": mold_id,
        "mold": mold_name,
    }


def _fetch_recent_cycles(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
    limit: int = 20,
) -> list[dict]:
    """Last N cycles only — for bucket charts / recent table (no full-window scan)."""
    rows = (
        db.query(Cycle.t_end, Cycle.cycle_time_s, Cycle.mold_id, Cycle.mold_name_snapshot)
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .order_by(Cycle.t_end.desc())
        .limit(limit)
        .all()
    )
    return enrich_series_mold_names(
        db, [_cycle_series_row_dict(r) for r in reversed(rows)]
    )


def _fetch_cycle_series(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
    max_points: int,
) -> tuple[list[dict], int, bool]:
    """Chronological cycles for zigzag charts (daily / calendar week)."""
    filt = (
        Cycle.machine_id == machine_id,
        Cycle.t_end >= start,
        Cycle.t_end <= end,
        Cycle.is_counted.is_(True),
    )
    total = int(db.query(func.count(Cycle.id)).filter(*filt).scalar() or 0)
    if total == 0:
        return [], 0, False

    cols = (Cycle.t_end, Cycle.cycle_time_s, Cycle.mold_id, Cycle.mold_name_snapshot)

    if total <= max_points:
        rows = db.query(*cols).filter(*filt).order_by(Cycle.t_end).all()
    else:
        # ceil(total / max_points) — avoid step=1 returning every row when total is just over max_points
        step = max(2, (total + max_points - 1) // max_points)
        rn = func.row_number().over(order_by=Cycle.t_end).label("rn")
        total_col = func.count().over().label("total")
        subq = select(*cols, rn, total_col).where(*filt).subquery()
        rows = (
            db.execute(
                select(subq.c.t_end, subq.c.cycle_time_s, subq.c.mold_id, subq.c.mold_name_snapshot)
                .where(
                    or_(
                        subq.c.rn == 1,
                        subq.c.rn == subq.c.total,
                        func.mod(subq.c.rn - 1, step) == 0,
                    )
                )
                .order_by(subq.c.t_end)
            )
            .all()
        )

    series = enrich_series_mold_names(db, [_cycle_series_row_dict(r) for r in rows])
    return series, total, total > len(series)


def _stats_from_trend_buckets(trend_buckets: list[dict], recent: list[dict]) -> dict:
    total = sum(int(b["cycle_count"]) for b in trend_buckets)
    if total == 0:
        return {
            "cycle_count": 0,
            "avg_cycle_s": 0.0,
            "min_cycle_s": 0.0,
            "max_cycle_s": 0.0,
            "last_cycle_s": 0.0,
        }
    sum_time = sum(int(b["cycle_count"]) * float(b["avg_cycle_s"]) for b in trend_buckets)
    return {
        "cycle_count": total,
        "avg_cycle_s": round(sum_time / total, 3),
        "min_cycle_s": round(min(float(b["min_cycle_s"]) for b in trend_buckets), 3),
        "max_cycle_s": round(max(float(b["max_cycle_s"]) for b in trend_buckets), 3),
        "last_cycle_s": round(float(recent[-1]["cycle_time_s"]), 3) if recent else 0.0,
    }


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
    start, end = resolve_window(range, from_ts, to_ts)
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


@router.get("/cycles_viewport")
def cycles_viewport(
    machine_id: int,
    db: Session = Depends(get_db),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
    max_points: int = Query(1200, ge=200, le=4000),
):
    """Load zigzag points for a visible scroll window (time-bounded, downsampled inside window only)."""
    if to_ts <= from_ts:
        to_ts = from_ts + timedelta(seconds=1)
    series, total, truncated = _fetch_cycle_series(db, machine_id, from_ts, to_ts, max_points)
    return {
        "machine_id": machine_id,
        "from": from_ts.isoformat(),
        "to": to_ts.isoformat(),
        "series": series,
        "cycle_count": total,
        "shown": len(series),
        "truncated": truncated,
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
    start, end = resolve_window(range, from_ts, to_ts)
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
        label = resolve_cycle_mold_label(db, r.mold_id, r.mold_name_snapshot) or "—"
        mold_counts[(r.mold_id, label)].append(float(r.cycle_time_s))

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
    series_limit: int = Query(80, ge=20, le=200),
    events_limit: int = Query(150, ge=10, le=1000),
    lazy_series: bool = Query(True),
):
    """Single fast payload for machine detail UI: aggregates + zigzag or bucket trend + events."""
    start, end = resolve_window(range, from_ts, to_ts)
    # Keep machine detail graph style consistent across all machines/ranges:
    # always use cycle-by-cycle zigzag presentation (same as Machine #2 view).
    chart_mode = "cycles"
    gap_threshold_s = 20 * 60  # 20 min — likely mold change / downtime between runs

    stats = _aggregate_cycle_stats(db, machine_id, start, end)
    total = stats["cycle_count"]
    series_lazy = True
    series = _fetch_recent_cycles(db, machine_id, start, end, 20)
    series_total = total
    series_truncated = total > 20
    trend_resolution = "cycle"
    trend_buckets = []
    trend_mold_names = []

    # Active mold: most recent cycle with a valid mold assignment in the window.
    _last_cycle = (
        db.query(Cycle.mold_id, Cycle.mold_name_snapshot)
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
            Cycle.mold_id.is_not(None),
        )
        .order_by(Cycle.t_end.desc())
        .limit(1)
        .first()
    )
    active_mold_id: int | None = None
    active_mold_name: str | None = None
    if _last_cycle and _last_cycle[0] is not None:
        mold_row = db.get(Mold, _last_cycle[0])
        if mold_row:
            active_mold_id = int(_last_cycle[0])
            active_mold_name = resolve_mold_display_name(db, active_mold_id, _last_cycle[1])

    if active_mold_id is not None:
        _am = (
            db.query(
                func.count(Cycle.id),
                func.avg(Cycle.cycle_time_s),
                func.min(Cycle.cycle_time_s),
                func.max(Cycle.cycle_time_s),
            )
            .filter(
                Cycle.machine_id == machine_id,
                Cycle.mold_id == active_mold_id,
                Cycle.t_end >= start,
                Cycle.t_end <= end,
                Cycle.is_counted.is_(True),
            )
            .one()
        )
        _am_cnt = int(_am[0] or 0)
        active_mold: dict | None = {
            "mold_id": active_mold_id,
            "mold_name": active_mold_name or f"#{active_mold_id}",
            "cycle_count": _am_cnt,
            "avg_cycle_s": round(float(_am[1] or 0), 3) if _am_cnt else 0.0,
            "min_cycle_s": round(float(_am[2] or 0), 3) if _am_cnt else 0.0,
            "max_cycle_s": round(float(_am[3] or 0), 3) if _am_cnt else 0.0,
        }
    else:
        active_mold = None

    if chart_mode == "cycles":
        mold_rows = (
            db.query(
                Cycle.mold_id,
                func.count(Cycle.id),
                func.avg(Cycle.cycle_time_s),
            )
            .filter(
                Cycle.machine_id == machine_id,
                Cycle.t_end >= start,
                Cycle.t_end <= end,
                Cycle.is_counted.is_(True),
            )
            .group_by(Cycle.mold_id)
            .order_by(func.count(Cycle.id).desc())
            .all()
        )
        mold_ids = {int(mid) for mid, _, _ in mold_rows if mid is not None}
        names_by_id = mold_name_map(db, mold_ids)
        mold_breakdown = []
        for mid, cnt, avg in mold_rows:
            if mid is None:
                label = "—"
            else:
                label = names_by_id.get(int(mid)) or f"#{mid}"
            mold_breakdown.append(
                {
                    "mold_id": mid,
                    "mold_name": label,
                    "cycle_count": int(cnt),
                    "share_pct": round(100.0 * int(cnt) / total, 2) if total else 0.0,
                    "avg_cycle_s": round(float(avg or 0), 3),
                }
            )
        if not trend_mold_names:
            trend_mold_names = sorted(
                {m["mold_name"] for m in mold_breakdown},
                key=str.casefold,
            )

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
            "created_at": _iso_utc_z(e.created_at) if e.created_at else None,
        }
        for e in ev_rows
    ]

    resolution_labels = {
        "hour": "Saatlik özet",
        "day": "Günlük özet",
        "month": "Aylık özet",
        "cycle": "Döngü döngüsü (zigzag)",
    }
    window_label = format_window_label(range, start, end)

    return {
        "machine_id": machine_id,
        "range": range,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "window_label": window_label,
        "chart_mode": chart_mode,
        "gap_threshold_s": gap_threshold_s,
        "summary": stats,
        "active_mold": active_mold,
        "mold_breakdown": mold_breakdown,
        "trend_resolution": trend_resolution,
        "trend_resolution_label": resolution_labels.get(trend_resolution, trend_resolution),
        "trend_buckets": trend_buckets,
        "trend_mold_names": trend_mold_names,
        "series": series,
        "series_total": series_total if chart_mode == "cycles" else total,
        "series_shown": len(series),
        "series_truncated": series_truncated,
        "series_lazy": series_lazy,
        "events": events,
    }


@router.get("/tv_board")
def tv_board(
    db: Session = Depends(get_db),
    machine_ids: str | None = Query(None, description="Comma-separated machine ids; empty = all enabled"),
):
    """Daily KPIs per machine for TV wall (single query, no per-machine dashboard load)."""
    start, end = resolve_window("daily", None, None)
    q_m = db.query(Machine).filter(Machine.enabled.is_(True))
    if machine_ids:
        ids = [int(x.strip()) for x in machine_ids.split(",") if x.strip().isdigit()]
        if ids:
            q_m = q_m.filter(Machine.id.in_(ids))
    machines = q_m.order_by(Machine.id).all()
    if not machines:
        return {
            "range": "daily",
            "from": start.isoformat(),
            "to": end.isoformat(),
            "window_label": format_window_label("daily", start, end),
            "machines": [],
        }

    mids = [m.id for m in machines]
    # Cycle.t_end is UTC in DB; bucket by Europe/Istanbul wall-clock hour (+3, no DST).
    hour_col = func.cast(
        func.strftime("%H", func.datetime(Cycle.t_end, "+3 hours")),
        Integer,
    ).label("hour")

    # Query 1: machine + hour → count (for chart; all molds)
    hourly_rows = (
        db.query(
            Cycle.machine_id,
            hour_col,
            func.count(Cycle.id),
        )
        .filter(
            Cycle.machine_id.in_(mids),
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .group_by(Cycle.machine_id, hour_col)
        .order_by(Cycle.machine_id, hour_col)
        .all()
    )

    hourly_acc: dict[int, dict[int, int]] = {}
    total_counts: dict[int, int] = {}
    for mid, hour, cnt in hourly_rows:
        mid_i = int(mid)
        cnt_i = int(cnt or 0)
        hourly_acc.setdefault(mid_i, {})[int(hour or 0)] = cnt_i
        total_counts[mid_i] = total_counts.get(mid_i, 0) + cnt_i

    # Query 2: find active mold per machine (latest cycle's mold_id today)
    latest_subq = (
        select(
            Cycle.machine_id,
            func.max(Cycle.t_end).label("max_tend"),
        )
        .where(
            Cycle.machine_id.in_(mids),
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .group_by(Cycle.machine_id)
        .subquery()
    )
    active_mold_rows = (
        db.query(Cycle.machine_id, Cycle.mold_id)
        .join(latest_subq, (Cycle.machine_id == latest_subq.c.machine_id) & (Cycle.t_end == latest_subq.c.max_tend))
        .filter(Cycle.is_counted.is_(True))
        .all()
    )
    active_mold_map: dict[int, int | None] = {int(r[0]): r[1] for r in active_mold_rows}

    # Collect unique non-null mold ids to fetch names
    mold_ids = {v for v in active_mold_map.values() if v is not None}
    mold_names: dict[int, str] = {}
    if mold_ids:
        for row in db.query(Mold.id, Mold.name).filter(Mold.id.in_(mold_ids)).all():
            mold_names[int(row[0])] = row[1] or f"#{row[0]}"

    # Query 3: stats for active mold only (per machine)
    # Build (machine_id, mold_id) pairs
    mid_mold_pairs = [(mid, moid) for mid, moid in active_mold_map.items() if moid is not None]
    mold_stats_map: dict[int, dict] = {}
    if mid_mold_pairs:
        from sqlalchemy import tuple_ as sa_tuple
        mold_stats_rows = (
            db.query(
                Cycle.machine_id,
                func.count(Cycle.id),
                func.avg(Cycle.cycle_time_s),
                func.min(Cycle.cycle_time_s),
                func.max(Cycle.cycle_time_s),
            )
            .filter(
                sa_tuple(Cycle.machine_id, Cycle.mold_id).in_(mid_mold_pairs),
                Cycle.t_end >= start,
                Cycle.t_end <= end,
                Cycle.is_counted.is_(True),
            )
            .group_by(Cycle.machine_id)
            .all()
        )
        for mid, cnt, avg, mn, mx in mold_stats_rows:
            mid_i = int(mid)
            cnt_i = int(cnt or 0)
            mold_stats_map[mid_i] = {
                "cycle_count": cnt_i,
                "avg_cycle_s": round(float(avg or 0), 2) if cnt_i else 0.0,
                "min_cycle_s": round(float(mn or 0), 2) if cnt_i else 0.0,
                "max_cycle_s": round(float(mx or 0), 2) if cnt_i else 0.0,
            }

    out: list[dict] = []
    for m in machines:
        total = total_counts.get(m.id, 0)
        active_mold_id = active_mold_map.get(m.id)
        active_mold_name = mold_names.get(active_mold_id) if active_mold_id else None
        mold_stats = mold_stats_map.get(m.id, {
            "cycle_count": 0, "avg_cycle_s": 0.0, "min_cycle_s": 0.0, "max_cycle_s": 0.0
        })

        h_data = hourly_acc.get(m.id, {})
        hourly = [
            {"hour": h, "count": h_data[h]}
            for h in sorted(h_data.keys())
        ]

        out.append(
            {
                "machine_id": m.id,
                "name": m.name,
                "total_cycle_count": total,
                "active_mold_id": active_mold_id,
                "active_mold_name": active_mold_name,
                "summary": mold_stats,
                "hourly": hourly,
            }
        )

    return {
        "range": "daily",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "window_label": format_window_label("daily", start, end),
        "machines": out,
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
