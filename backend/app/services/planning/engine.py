"""Mold×machine performance cards (read-only; off vision hot path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Cycle, MachineDowntime, Mold, MoldMachineCapability, MoldRun
from app.services.planning import effective_cavity


def mold_machine_stats(db: Session, *, days: int = 30) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    runs = (
        db.query(MoldRun)
        .filter(MoldRun.ended_at.isnot(None), MoldRun.started_at >= since)
        .all()
    )
    # Aggregate by (mold_id, machine_id)
    buckets: dict[tuple[int, int], dict] = {}
    for r in runs:
        key = (int(r.mold_id), int(r.machine_id))
        b = buckets.setdefault(
            key,
            {
                "mold_id": key[0],
                "machine_id": key[1],
                "run_count": 0,
                "work_s": 0.0,
                "span_s": 0.0,
                "camera_cycles": 0,
                "product_added": 0,
                "press_entered": 0,
            },
        )
        b["run_count"] += 1
        start = r.started_at
        end = r.ended_at or start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        span = max(0.0, (end - start).total_seconds())
        b["span_s"] += span
        # Approximate work time from counted cycles duration if available
        cycle_sum = (
            db.query(func.coalesce(func.sum(Cycle.cycle_time_s), 0.0))
            .filter(
                Cycle.machine_id == r.machine_id,
                Cycle.mold_id == r.mold_id,
                Cycle.is_counted.is_(True),
                Cycle.t_end >= start,
                Cycle.t_end <= end,
            )
            .scalar()
        )
        b["work_s"] += float(cycle_sum or 0.0)
        b["camera_cycles"] += int(r.camera_cycles or 0)
        b["product_added"] += int(r.product_added or 0)
        b["press_entered"] += int(r.entered_press_count or 0)

    molds = {m.id: m for m in db.query(Mold).all()}
    caps = {
        (c.mold_id, c.machine_id): c
        for c in db.query(MoldMachineCapability).filter(MoldMachineCapability.allowed.is_(True)).all()
    }
    out: list[dict] = []
    for (mold_id, machine_id), b in buckets.items():
        mold = molds.get(mold_id)
        span = b["span_s"] or 1.0
        work = min(b["work_s"], span)
        run_ratio = work / span if span > 0 else 0.0
        target = None
        if mold:
            target = mold.target_cycle_s or (mold.avg_cycle_s if mold.avg_cycle_s > 0 else None)
        avg_cycle = None
        if b["camera_cycles"] > 0 and work > 0:
            avg_cycle = work / b["camera_cycles"]
        cycle_gap = None
        if target and avg_cycle:
            cycle_gap = (avg_cycle - float(target)) / float(target)
        expected_from_cam = None
        cavity = effective_cavity(mold) if mold else 1
        if b["camera_cycles"]:
            expected_from_cam = b["camera_cycles"] * cavity
        count_diff = None
        if b["press_entered"] and b["camera_cycles"]:
            count_diff = b["press_entered"] - b["camera_cycles"]
        cap = caps.get((mold_id, machine_id))
        # Priority change suggestion: if run_ratio high and cycle_gap low vs current priority
        priority_suggestion = None
        if cap and run_ratio >= 0.7 and (cycle_gap is None or cycle_gap <= 0.1):
            if cap.priority > 1:
                priority_suggestion = {
                    "current_priority": cap.priority,
                    "suggested_priority": max(1, cap.priority - 1),
                    "reason": "Yüksek çalışma oranı ve iyi çevrim; öncelik yükseltilebilir",
                }

        out.append(
            {
                "mold_id": mold_id,
                "mold_name": mold.name if mold else None,
                "machine_id": machine_id,
                "run_count": b["run_count"],
                "run_ratio": round(run_ratio, 3),
                "avg_cycle_s": round(avg_cycle, 2) if avg_cycle else None,
                "target_cycle_s": float(target) if target else None,
                "cycle_gap": round(cycle_gap, 3) if cycle_gap is not None else None,
                "camera_cycles": b["camera_cycles"],
                "product_added": b["product_added"],
                "expected_from_camera": expected_from_cam,
                "count_diff": count_diff,  # neutral; no fire/camera label
                "priority": cap.priority if cap else None,
                "priority_suggestion": priority_suggestion,
            }
        )
    out.sort(key=lambda x: (-(x["run_ratio"] or 0), x["mold_id"], x["machine_id"]))
    return out


def open_downtime_machine_ids(db: Session) -> set[int]:
    """Machines with open-ended downtime (end_at NULL) — excluded from plan candidates."""
    rows = (
        db.query(MachineDowntime.machine_id)
        .filter(MachineDowntime.end_at.is_(None))
        .distinct()
        .all()
    )
    return {int(r[0]) for r in rows}


# Back-compat alias
open_fault_machine_ids = open_downtime_machine_ids
