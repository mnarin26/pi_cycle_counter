"""Global mold suggestions, matching, weighted averages."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Machine, Mold, MoldMachine, json_dumps


WINDOW = 12
BIN_WIDTH = 0.1


def _confidence_from_samples(values: list[float]) -> float:
    if len(values) < 3:
        return 0.3
    try:
        st = statistics.pstdev(values)
        return max(0.0, min(1.0, 1.0 - min(st / max(statistics.mean(values), 0.1), 1.0)))
    except statistics.StatisticsError:
        return 0.5


def suggest_or_match_cycles(
    db: Session,
    machine: Machine,
    cycle_s: float,
    rolling: dict[int, list[float]],
) -> list[dict[str, Any]]:
    """Returns list of side-effect descriptions; caller applies DB changes."""
    actions: list[dict[str, Any]] = []
    mid = machine.id
    rolling.setdefault(mid, []).append(cycle_s)
    rolling[mid] = rolling[mid][-WINDOW:]

    mold = None
    if machine.current_mold_id:
        mold = db.get(Mold, machine.current_mold_id)
        if mold and mold.status == "ignored":
            mold = None

    if mold and mold.status == "active" and mold.name:
        d = abs(cycle_s - mold.avg_cycle_s)
        if d <= mold.tolerance_s:
            actions.append(
                {
                    "type": "update_mold_weighted",
                    "mold_id": mold.id,
                    "new_sample": cycle_s,
                }
            )
            return actions
        actions.append(
            {
                "type": "event",
                "event": {
                    "type": "mold_match_prompt",
                    "machine_id": mid,
                    "payload": json_dumps(
                        {
                            "message": "Possible match detected. Use existing mold?",
                            "mold_id": mold.id,
                            "mold_name": mold.name,
                            "cycle_s": cycle_s,
                            "avg_s": mold.avg_cycle_s,
                            "delta_s": d,
                        }
                    ),
                },
            }
        )
        return actions

    if len(rolling[mid]) < 5:
        return actions

    mean_v = statistics.mean(rolling[mid])
    st = statistics.pstdev(rolling[mid]) if len(rolling[mid]) > 1 else 0.2
    if st > 0.35:
        return actions

    if machine.current_mold_id:
        cm = db.get(Mold, machine.current_mold_id)
        if cm and cm.status == "candidate" and abs(cm.avg_cycle_s - mean_v) < 0.35:
            return actions

    tol = max(0.2, 0.05 * mean_v)
    existing = (
        db.query(Mold)
        .filter(Mold.status.in_(["active", "candidate"]))
        .filter(Mold.name.is_(None))
        .all()
    )
    for m in existing:
        if abs(m.avg_cycle_s - mean_v) < BIN_WIDTH * 2 and m.sample_count >= 3:
            return actions

    conf = _confidence_from_samples(rolling[mid])
    actions.append(
        {
            "type": "create_candidate_mold",
            "machine_id": mid,
            "avg_cycle_s": mean_v,
            "tolerance_s": tol,
            "sample_count": len(rolling[mid]),
            "confidence": conf,
        }
    )
    actions.append(
        {
            "type": "event",
            "event": {
                "type": "mold_suggestion",
                "machine_id": mid,
                "payload": json_dumps(
                    {
                        "message": "Unnamed Mold Suggestion",
                        "avg_cycle_s": mean_v,
                        "tolerance_s": tol,
                        "confidence": conf,
                    }
                ),
            },
        }
    )
    return actions


def apply_weighted_average(db: Session, mold: Mold, sample: float) -> None:
    n = max(1, mold.sample_count)
    new_avg = (n * mold.avg_cycle_s + sample) / (n + 1)
    mold.avg_cycle_s = new_avg
    mold.sample_count = n + 1
    mold.confidence = min(1.0, mold.confidence + 0.02)
    mold.updated_at = datetime.now(timezone.utc)


def link_mold_machine(db: Session, mold_id: int, machine_id: int) -> None:
    row = (
        db.query(MoldMachine)
        .filter_by(mold_id=mold_id, machine_id=machine_id)
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if row:
        row.last_seen_at = now
        row.cycles_attributed = row.cycles_attributed + 1
    else:
        db.add(
            MoldMachine(
                mold_id=mold_id,
                machine_id=machine_id,
                first_seen_at=now,
                last_seen_at=now,
                cycles_attributed=1,
            )
        )


def handle_cycle_completion(
    db: Session,
    machine: Machine,
    cycle_s: float,
    t_start: datetime,
    t_end: datetime,
    rolling: dict[int, list[float]],
    mold_name_snapshot: str | None,
    confidence: float,
) -> None:
    actions = suggest_or_match_cycles(db, machine, cycle_s, rolling)
    mold_id = machine.current_mold_id
    mold = db.get(Mold, mold_id) if mold_id else None

    for a in actions:
        if a["type"] == "update_mold_weighted":
            m = db.get(Mold, a["mold_id"])
            if m:
                apply_weighted_average(db, m, a["new_sample"])
                link_mold_machine(db, m.id, machine.id)
        elif a["type"] == "create_candidate_mold":
            nm = Mold(
                name=None,
                status="candidate",
                avg_cycle_s=a["avg_cycle_s"],
                tolerance_s=a["tolerance_s"],
                sample_count=a["sample_count"],
                confidence=a["confidence"],
            )
            db.add(nm)
            db.flush()
            machine.current_mold_id = nm.id
            mold_id = nm.id
            link_mold_machine(db, nm.id, machine.id)
            rolling[machine.id] = []
        elif a["type"] == "event":
            ev = a["event"]
            db.add(Event(type=ev["type"], machine_id=ev.get("machine_id"), payload=ev.get("payload")))

    db.flush()
    machine = db.get(Machine, machine.id)
    mold_id = machine.current_mold_id if machine else mold_id
    mold = db.get(Mold, mold_id) if mold_id else None
    if mold and mold.status == "active" and mold.name:
        mold_name_snapshot = mold.name

    c = Cycle(
        machine_id=machine.id,
        mold_id=mold_id,
        cycle_time_s=cycle_s,
        t_start=t_start,
        t_end=t_end,
        confidence=confidence,
        mold_name_snapshot=mold_name_snapshot,
    )
    db.add(c)

    if cycle_s > 0 and mold_id:
        m2 = db.get(Mold, mold_id)
        if m2 and m2.avg_cycle_s > 0 and m2.tolerance_s > 0:
            if abs(cycle_s - m2.avg_cycle_s) > max(m2.tolerance_s * 4, 1.5):
                db.add(
                    Event(
                        type="abnormal_cycle",
                        machine_id=machine.id,
                        payload=json_dumps({"cycle_s": cycle_s, "expected": m2.avg_cycle_s}),
                    )
                )

    db.commit()
