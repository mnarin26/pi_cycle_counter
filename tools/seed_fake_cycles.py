#!/usr/bin/env python3
"""Seed realistic fake cycle history for dashboard / machine detail demos."""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Run from repo: python tools/seed_fake_cycles.py
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.db.session as db_session  # noqa: E402
from app.db.models import Cycle, Event, Machine, Mold, MoldMachine  # noqa: E402

MOLD_PROFILES = [
    ("Kalip-A (kucuk parca)", 9.2, 1.1),
    ("Kalip-B (orta)", 11.5, 1.4),
    ("Kalip-C (buyuk)", 13.8, 1.6),
    ("Kalip-D (ince)", 10.1, 1.2),
]

EVENT_TYPES = [
    "no_movement",
    "cycle_outlier",
    "reflector_weak",
]


def _ensure_molds(db, machine_id: int) -> list[Mold]:
    molds: list[Mold] = []
    for name, avg_s, tol in MOLD_PROFILES:
        existing = db.query(Mold).filter(Mold.name == name).first()
        if existing:
            m = existing
        else:
            m = Mold(
                name=name,
                status="active",
                avg_cycle_s=avg_s,
                tolerance_s=tol,
                sample_count=0,
                confidence=0.85,
            )
            db.add(m)
            db.flush()
        link = (
            db.query(MoldMachine)
            .filter(MoldMachine.machine_id == machine_id, MoldMachine.mold_id == m.id)
            .first()
        )
        if not link:
            db.add(MoldMachine(machine_id=machine_id, mold_id=m.id, cycles_attributed=0))
        molds.append(m)
    db.commit()
    return molds


def _pick_mold(molds: list[Mold], hour: int, rng: random.Random) -> Mold:
    # Shift pattern: morning more A, afternoon more B/C
    weights = [1.0, 1.0, 1.0, 1.0]
    if 6 <= hour < 12:
        weights = [3.0, 1.5, 0.8, 1.2]
    elif 12 <= hour < 18:
        weights = [0.8, 2.0, 2.5, 1.0]
    else:
        weights = [1.2, 1.2, 1.5, 2.0]
    return rng.choices(molds, weights=weights, k=1)[0]


def _cycle_duration_s(mold: Mold, rng: random.Random) -> float:
    base = float(mold.avg_cycle_s or 10.0)
    tol = float(mold.tolerance_s or 1.0)
    # Mostly normal, sometimes slow (tool change / hesitation)
    if rng.random() < 0.04:
        return round(base + rng.uniform(6.0, 14.0), 3)
    if rng.random() < 0.02:
        return round(max(4.0, base - rng.uniform(2.0, 4.0)), 3)
    return round(max(4.5, rng.gauss(base, tol * 0.45)), 3)


def seed(
    machine_id: int,
    days: int,
    replace: bool,
    seed_rng: int | None,
) -> dict[str, int]:
    db_session.get_engine()
    db_session.init_db()
    rng = random.Random(seed_rng)
    db = db_session.SessionLocal()
    try:
        machine = db.get(Machine, machine_id)
        if not machine:
            raise SystemExit(f"Machine {machine_id} not found")

        if replace:
            deleted_cycles = (
                db.query(Cycle).filter(Cycle.machine_id == machine_id).delete(synchronize_session=False)
            )
            deleted_events = (
                db.query(Event).filter(Event.machine_id == machine_id).delete(synchronize_session=False)
            )
        else:
            deleted_cycles = 0
            deleted_events = 0

        molds = _ensure_molds(db, machine_id)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        t = start
        inserted = 0
        batch: list[Cycle] = []
        mold_use: dict[int, int] = {m.id: 0 for m in molds}

        while t < now:
            hour = t.hour
            # Low activity overnight
            if hour < 5:
                t += timedelta(minutes=rng.randint(25, 55))
                continue
            if hour >= 23:
                t += timedelta(minutes=rng.randint(15, 40))
                continue

            # Weekend slightly slower line
            if t.weekday() >= 5 and rng.random() < 0.35:
                t += timedelta(minutes=rng.randint(8, 25))
                continue

            mold = _pick_mold(molds, hour, rng)
            dur = _cycle_duration_s(mold, rng)
            t_end = t + timedelta(seconds=dur)
            if t_end >= now:
                break

            batch.append(
                Cycle(
                    machine_id=machine_id,
                    mold_id=mold.id,
                    cycle_time_s=dur,
                    t_start=t,
                    t_end=t_end,
                    confidence=round(rng.uniform(0.72, 0.98), 3),
                    mold_name_snapshot=mold.name,
                    is_counted=True,
                    exclude_reason=None,
                )
            )
            mold_use[mold.id] = mold_use.get(mold.id, 0) + 1
            inserted += 1

            # Gap between cycles: mostly 0-3s idle + occasional longer pause
            gap_s = rng.expovariate(1.0 / 2.2)
            if rng.random() < 0.06:
                gap_s += rng.uniform(30.0, 180.0)  # short stop
            t = t_end + timedelta(seconds=max(0.5, gap_s))

            if len(batch) >= 500:
                db.add_all(batch)
                db.commit()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.commit()

        # Mold stats + current mold on machine
        for m in molds:
            m.sample_count = mold_use.get(m.id, 0)
            if m.sample_count:
                m.confidence = min(0.99, 0.7 + m.sample_count / 500)
        machine.current_mold_id = max(mold_use, key=mold_use.get) if mold_use else molds[0].id
        for m in molds:
            link = (
                db.query(MoldMachine)
                .filter(MoldMachine.machine_id == machine_id, MoldMachine.mold_id == m.id)
                .first()
            )
            if link:
                link.cycles_attributed = mold_use.get(m.id, 0)
                link.last_seen_at = now
        db.commit()

        # Scatter a few events
        events_added = 0
        for _ in range(max(12, days // 2)):
            ev_t = start + timedelta(seconds=rng.randint(0, int((now - start).total_seconds())))
            db.add(
                Event(
                    type=rng.choice(EVENT_TYPES),
                    machine_id=machine_id,
                    payload='{"demo": true}',
                    created_at=ev_t,
                )
            )
            events_added += 1
        db.commit()

        return {
            "machine_id": machine_id,
            "days": days,
            "deleted_cycles": int(deleted_cycles),
            "deleted_events": int(deleted_events),
            "inserted_cycles": inserted,
            "events_added": events_added,
            "molds": len(molds),
        }
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Seed fake cycle data for a machine")
    p.add_argument("--machine-id", type=int, default=1)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--no-replace", action="store_true", help="Append instead of replacing machine history")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    stats = seed(args.machine_id, args.days, replace=not args.no_replace, seed_rng=args.seed)
    print(stats)


if __name__ == "__main__":
    main()
