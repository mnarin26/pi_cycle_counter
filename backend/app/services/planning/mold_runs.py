"""Close/open mold_runs on assign; optional actual press count → stock."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Mold, MoldRun
from app.services.planning import effective_cavity
from app.services.planning.stock import add_movement

logger = logging.getLogger(__name__)

# If last counted cycle is older than this before the new assign, close at last cycle.
LONG_STOP_THRESHOLD = timedelta(minutes=30)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def open_run(db: Session, *, mold_id: int, machine_id: int, started_at: datetime) -> MoldRun:
    run = MoldRun(
        mold_id=int(mold_id),
        machine_id=int(machine_id),
        started_at=_aware(started_at),
        ended_at=None,
    )
    db.add(run)
    db.flush()
    return run


def _count_camera_cycles(
    db: Session, *, machine_id: int, mold_id: int, start: datetime, end: datetime
) -> int:
    return (
        db.query(Cycle)
        .filter(
            Cycle.machine_id == int(machine_id),
            Cycle.mold_id == int(mold_id),
            Cycle.is_counted.is_(True),
            Cycle.t_end >= _aware(start),
            Cycle.t_end <= _aware(end),
        )
        .count()
    )


def _last_cycle_end(
    db: Session, *, machine_id: int, mold_id: int, start: datetime, before: datetime
) -> datetime | None:
    row = (
        db.query(Cycle.t_end)
        .filter(
            Cycle.machine_id == int(machine_id),
            Cycle.mold_id == int(mold_id),
            Cycle.is_counted.is_(True),
            Cycle.t_end >= _aware(start),
            Cycle.t_end <= _aware(before),
        )
        .order_by(Cycle.t_end.desc())
        .first()
    )
    return _aware(row[0]) if row and row[0] else None


def close_run(
    db: Session,
    run: MoldRun,
    *,
    assign_at: datetime,
    entered_press_count: int | None = None,
) -> MoldRun:
    """Close run using long-stop vs near-changeover rule; optionally add stock."""
    when = _aware(assign_at)
    start = _aware(run.started_at)
    last = _last_cycle_end(
        db, machine_id=run.machine_id, mold_id=run.mold_id, start=start, before=when
    )
    if last is not None and (when - last) > LONG_STOP_THRESHOLD:
        ended_at = last
        end_reason = "long_stop"
    else:
        ended_at = when
        end_reason = "mold_change"

    camera_cycles = _count_camera_cycles(
        db,
        machine_id=run.machine_id,
        mold_id=run.mold_id,
        start=start,
        end=ended_at,
    )
    mold = db.get(Mold, run.mold_id)
    cavity = effective_cavity(mold) if mold else 1
    product_added: int | None = None
    if entered_press_count is not None and entered_press_count >= 0:
        product_added = int(entered_press_count) * cavity
        if mold and mold.product_id and product_added > 0:
            add_movement(
                db,
                product_id=int(mold.product_id),
                qty=product_added,
                kind="production",
                ref_type="mold_run",
                ref_id=run.id,
                note=f"Gerçek baskı {entered_press_count} × göz {cavity}",
                created_at=ended_at,
            )

    run.ended_at = ended_at
    run.end_reason = end_reason
    run.camera_cycles = int(camera_cycles)
    run.entered_press_count = entered_press_count
    run.active_cavity_snapshot = cavity
    run.product_added = product_added
    db.flush()
    return run


def count_diff_info(run: MoldRun) -> dict | None:
    """Neutral camera vs entered press difference (no fire/camera labels)."""
    if run.camera_cycles is None or run.entered_press_count is None:
        return None
    cam = int(run.camera_cycles)
    ent = int(run.entered_press_count)
    diff = ent - cam
    pct = (diff / cam * 100.0) if cam else None
    return {
        "camera_cycles": cam,
        "entered_press_count": ent,
        "diff": diff,
        "diff_pct": round(pct, 1) if pct is not None else None,
    }


def close_open_runs_for_machine(
    db: Session,
    *,
    machine_id: int,
    assign_at: datetime,
    entered_press_count: int | None = None,
) -> list[MoldRun]:
    """Close any open runs on this machine (previous mold) before a new assign."""
    open_runs = (
        db.query(MoldRun)
        .filter(MoldRun.machine_id == int(machine_id), MoldRun.ended_at.is_(None))
        .all()
    )
    closed: list[MoldRun] = []
    for run in open_runs:
        # Only apply entered press to the most recent open run (previous mold).
        press = entered_press_count if not closed else None
        closed.append(close_run(db, run, assign_at=assign_at, entered_press_count=press))
    return closed


def ensure_open_run_from_assign(
    db: Session, *, machine_id: int, mold_id: int, started_at: datetime
) -> MoldRun:
    existing = (
        db.query(MoldRun)
        .filter(
            MoldRun.machine_id == int(machine_id),
            MoldRun.mold_id == int(mold_id),
            MoldRun.ended_at.is_(None),
        )
        .first()
    )
    if existing:
        return existing
    return open_run(db, mold_id=mold_id, machine_id=machine_id, started_at=started_at)


def find_open_run(db: Session, machine_id: int) -> MoldRun | None:
    return (
        db.query(MoldRun)
        .filter(MoldRun.machine_id == int(machine_id), MoldRun.ended_at.is_(None))
        .order_by(MoldRun.started_at.desc())
        .first()
    )
