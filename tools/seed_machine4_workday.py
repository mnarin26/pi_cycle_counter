#!/usr/bin/env python3
"""Machine #4 — one workday of cycles without mold assignment (matcher observes later)."""

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
from app.db.models import Cycle, Event, Machine  # noqa: E402

MACHINE_ID = 4
UTC = timezone.utc
IST = ZoneInfo("Europe/Istanbul")

# (start_h, start_m, end_h, end_m, mode)  mode: "run" | "stop"
# end 23:59 handled as end of local calendar day
SEGMENTS: list[tuple[int, int, int, int, str, tuple[float, float] | None]] = [
    (0, 0, 3, 0, "run", (10.0, 11.0)),
    (3, 0, 3, 30, "stop", None),
    (3, 30, 7, 55, "run", (13.0, 15.0)),
    (7, 55, 8, 0, "stop", None),
    (8, 0, 10, 0, "run", (13.0, 15.0)),
    (10, 0, 10, 45, "stop", None),
    (10, 45, 17, 0, "run", (13.0, 15.0)),
    (17, 0, 17, 45, "stop", None),
    (17, 45, 23, 59, "run", (16.0, 17.0)),
]


def _segment_bounds(day_local: datetime, h0: int, m0: int, h1: int, m1: int) -> tuple[datetime, datetime]:
    a = day_local + timedelta(hours=h0, minutes=m0)
    b = day_local + timedelta(hours=h1, minutes=m1)
    return a.astimezone(UTC), b.astimezone(UTC)


def _emit_window(
    rng: random.Random,
    batch: list[Cycle],
    t0_utc: datetime,
    t1_utc: datetime,
    dur_range: tuple[float, float],
) -> int:
    t = t0_utc
    lo, hi = dur_range
    n = 0
    while t < t1_utc:
        dur = round(rng.uniform(lo, hi), 3)
        t_end = t + timedelta(seconds=dur)
        if t_end > t1_utc:
            break
        batch.append(
            Cycle(
                machine_id=MACHINE_ID,
                mold_id=None,
                cycle_time_s=dur,
                t_start=t,
                t_end=t_end,
                confidence=round(rng.uniform(0.85, 0.98), 3),
                mold_name_snapshot=None,
                is_counted=True,
                exclude_reason=None,
            )
        )
        n += 1
        gap_s = rng.expovariate(1.0 / 1.8)
        t = t_end + timedelta(seconds=max(0.4, gap_s))
    return n


def seed() -> dict:
    db_session.get_engine()
    db = db_session.SessionLocal()
    rng = random.Random(4042026)
    try:
        machine = db.get(Machine, MACHINE_ID)
        if not machine:
            raise SystemExit(f"Machine {MACHINE_ID} not found")

        deleted_c = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).delete(synchronize_session=False)
        deleted_e = db.query(Event).filter(Event.machine_id == MACHINE_ID).delete(synchronize_session=False)
        db.commit()

        now_utc = datetime.now(UTC)
        local = now_utc.astimezone(IST)
        day_local = datetime(local.year, local.month, local.day, tzinfo=IST)
        day_end_utc = min((day_local + timedelta(days=1)).astimezone(UTC), now_utc)

        machine.current_mold_id = None
        machine.enabled = True
        db.commit()

        batch: list[Cycle] = []
        segment_counts: list[dict] = []

        for h0, m0, h1, m1, mode, dur_range in SEGMENTS:
            s0, s1 = _segment_bounds(day_local, h0, m0, h1, m1)
            if s1 <= s0:
                continue
            if s0 >= day_end_utc:
                break
            s1 = min(s1, day_end_utc)
            if mode == "stop":
                segment_counts.append(
                    {
                        "window": f"{h0:02d}:{m0:02d}-{h1:02d}:{m1:02d} stop",
                        "cycles": 0,
                    }
                )
                continue
            assert dur_range is not None
            n = _emit_window(rng, batch, s0, s1, dur_range)
            segment_counts.append(
                {
                    "window": f"{h0:02d}:{m0:02d}-{h1:02d}:{m1:02d} run {dur_range[0]}-{dur_range[1]}s",
                    "cycles": n,
                }
            )
            if len(batch) >= 800:
                db.add_all(batch)
                db.commit()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.commit()

        total = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).count()
        with_mold = (
            db.query(Cycle)
            .filter(Cycle.machine_id == MACHINE_ID, Cycle.mold_id.is_not(None))
            .count()
        )

        return {
            "machine_id": MACHINE_ID,
            "deleted_cycles": int(deleted_c),
            "deleted_events": int(deleted_e),
            "inserted_cycles": int(total),
            "cycles_with_mold_id": int(with_mold),
            "current_mold_id": machine.current_mold_id,
            "day_istanbul": day_local.isoformat(),
            "segments": segment_counts,
        }
    finally:
        db.close()


if __name__ == "__main__":
    print(seed())
