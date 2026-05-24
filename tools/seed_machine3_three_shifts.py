#!/usr/bin/env python3
"""Seed 1-day, 3-shift, 3-mold production history for machine #3 (Istanbul shifts)."""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.db.session as db_session  # noqa: E402
from app.db.models import Cycle, Event, Machine, Mold, MoldMachine  # noqa: E402

MACHINE_ID = 3
UTC = timezone.utc
IST = ZoneInfo("Europe/Istanbul")
MOLD_NAMES = ("Kalip-3A", "Kalip-3B", "Kalip-3C")
CHANGEOVER_MIN = 25  # kalıp değişimi: döngü yok


def _ensure_molds(db) -> list[Mold]:
    molds: list[Mold] = []
    for name, avg_s in zip(MOLD_NAMES, (9.8, 11.6, 13.2), strict=True):
        mold = db.query(Mold).filter(Mold.name == name).first()
        if not mold:
            mold = Mold(
                name=name,
                status="active",
                avg_cycle_s=avg_s,
                tolerance_s=1.1,
                sample_count=0,
                confidence=0.9,
            )
            db.add(mold)
            db.flush()
        link = (
            db.query(MoldMachine)
            .filter(MoldMachine.machine_id == MACHINE_ID, MoldMachine.mold_id == mold.id)
            .first()
        )
        if not link:
            db.add(MoldMachine(machine_id=MACHINE_ID, mold_id=mold.id, cycles_attributed=0))
        molds.append(mold)
    db.commit()
    return molds


def _break_windows_local(day_local: datetime) -> list[tuple[datetime, datetime]]:
    """Mola pencereleri İstanbul saatiyle; döndürülen değerler UTC."""
    mins = [
        (2, 0, 12),
        (4, 0, 30),
        (6, 0, 12),
        (10, 0, 12),
        (12, 0, 30),
        (14, 0, 12),
        (18, 0, 12),
        (20, 0, 30),
        (22, 0, 12),
    ]
    out: list[tuple[datetime, datetime]] = []
    for h, m, dur in mins:
        a_local = day_local + timedelta(hours=h, minutes=m)
        b_local = a_local + timedelta(minutes=dur)
        out.append((a_local.astimezone(UTC), b_local.astimezone(UTC)))
    return out


def _advance_over_breaks(t: datetime, windows: list[tuple[datetime, datetime]]) -> datetime:
    moved = True
    while moved:
        moved = False
        for a, b in windows:
            if a <= t < b:
                t = b
                moved = True
    return t


def _overlaps_break(t0: datetime, t1: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    for a, b in windows:
        if t0 < b and t1 > a:
            return True
    return False


def seed() -> dict:
    db_session.get_engine()
    db = db_session.SessionLocal()
    rng = random.Random(3032026)
    try:
        machine = db.get(Machine, MACHINE_ID)
        if not machine:
            raise SystemExit(f"Machine {MACHINE_ID} not found")

        deleted_cycles = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).delete(synchronize_session=False)
        deleted_events = db.query(Event).filter(Event.machine_id == MACHINE_ID).delete(synchronize_session=False)
        db.commit()

        molds = _ensure_molds(db)

        now_utc = datetime.now(UTC)
        local = now_utc.astimezone(IST)
        day_local = datetime(local.year, local.month, local.day, tzinfo=IST)
        day_end_local = day_local + timedelta(days=1)
        day_start_utc = day_local.astimezone(UTC)
        day_end_utc = min(day_end_local.astimezone(UTC), now_utc)

        breaks = _break_windows_local(day_local)

        # Üretim pencereleri (İstanbul) — vardiya sınırlarında changeover boşluğu
        prod_local = [
            (day_local + timedelta(hours=0), day_local + timedelta(hours=8) - timedelta(minutes=CHANGEOVER_MIN), molds[0]),
            (
                day_local + timedelta(hours=8) + timedelta(minutes=CHANGEOVER_MIN),
                day_local + timedelta(hours=16) - timedelta(minutes=CHANGEOVER_MIN),
                molds[1],
            ),
            (
                day_local + timedelta(hours=16) + timedelta(minutes=CHANGEOVER_MIN),
                min(day_end_local, local),
                molds[2],
            ),
        ]
        shifts = [(a.astimezone(UTC), b.astimezone(UTC), m) for a, b, m in prod_local if a < b]

        counts: dict[int, int] = {m.id: 0 for m in molds}
        batch: list[Cycle] = []
        for s0, s1, mold in shifts:
            if s1 <= s0:
                continue
            t = _advance_over_breaks(s0, breaks)
            base = float(mold.avg_cycle_s or 10.0)
            while t < s1:
                dur = max(4.5, rng.gauss(base, 1.0))
                if rng.random() < 0.02:
                    dur += rng.uniform(4.0, 9.0)
                t_end = t + timedelta(seconds=dur)
                if t_end > s1:
                    break
                if _overlaps_break(t, t_end, breaks):
                    t = _advance_over_breaks(t_end, breaks)
                    continue

                batch.append(
                    Cycle(
                        machine_id=MACHINE_ID,
                        mold_id=mold.id,
                        cycle_time_s=round(dur, 3),
                        t_start=t,
                        t_end=t_end,
                        confidence=round(rng.uniform(0.82, 0.98), 3),
                        mold_name_snapshot=mold.name,
                        is_counted=True,
                    )
                )
                counts[mold.id] += 1
                gap_s = rng.expovariate(1.0 / 1.7)
                t = _advance_over_breaks(t_end + timedelta(seconds=max(0.5, gap_s)), breaks)

            if len(batch) >= 800:
                db.add_all(batch)
                db.commit()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.commit()

        for m in molds:
            c = counts.get(m.id, 0)
            m.sample_count = c
            link = (
                db.query(MoldMachine)
                .filter(MoldMachine.machine_id == MACHINE_ID, MoldMachine.mold_id == m.id)
                .first()
            )
            if link:
                link.cycles_attributed = c
                link.last_seen_at = now_utc

        machine.current_mold_id = molds[2].id
        machine.enabled = True
        db.commit()

        inserted = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).count()
        return {
            "machine_id": MACHINE_ID,
            "deleted_cycles": int(deleted_cycles),
            "deleted_events": int(deleted_events),
            "inserted_cycles": int(inserted),
            "day_start_istanbul": day_local.isoformat(),
            "day_end_utc": day_end_utc.isoformat(),
            "shift_boundaries_istanbul": "00-08 3A | 08:00-08:25 changeover | 08:25-16 3B | 16:00-16:25 changeover | 16:25+ 3C",
            "molds": [m.name for m in molds],
            "counts": {str(k): int(v) for k, v in counts.items()},
        }
    finally:
        db.close()


if __name__ == "__main__":
    print(seed())
