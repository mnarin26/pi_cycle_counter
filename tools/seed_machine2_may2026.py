#!/usr/bin/env python3
"""Scenario seed for machine #2 — May 2026 with mold runs, changeovers, and shift breaks."""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.db.session as db_session  # noqa: E402
from app.db.models import Cycle, Event, Machine, Mold, MoldMachine  # noqa: E402

MACHINE_ID = 2
TZ = timezone.utc
START = datetime(2026, 5, 1, 0, 0, 0, tzinfo=TZ)
END = datetime(2026, 6, 1, 0, 0, 0, tzinfo=TZ)  # all May

MOLDS = {
    "k1": "Kalip-1",
    "k2": "Kalip-2",
}

# (mold_key | None for gap, duration)
PRODUCTION_PLAN: list[tuple[str | None, timedelta]] = [
    ("k1", timedelta(hours=2)),
    (None, timedelta(minutes=15)),  # durus / hazirlik
    ("k1", timedelta(days=1)),
    (None, timedelta(minutes=50)),  # kalip degisimi oncesi
    ("k2", timedelta(days=4)),
    (None, timedelta(minutes=40)),
    ("k2", timedelta(days=10)),
    (None, timedelta(hours=1)),
    ("k1", timedelta(days=3)),
    (None, timedelta(minutes=35)),
    ("k2", timedelta(days=6)),
    (None, timedelta(minutes=45)),
    ("k2", timedelta(days=5)),
    (None, timedelta(minutes=55)),  # uzun kalip degisimi
    ("k1", timedelta(days=4)),
    (None, timedelta(minutes=30)),
    ("k2", timedelta(days=3)),
]

# 3 vardiya: gunde 3 yemek, 6 cay molasi (dakika, sure dakika)
DAILY_MEALS = [(12, 0, 35), (20, 0, 35), (4, 0, 35)]  # saat, dk, sure
DAILY_TEAS = [(9, 30, 12), (11, 0, 12), (15, 30, 12), (17, 0, 12), (21, 30, 12), (23, 0, 12)]

# Bazi gunler operatör sapmalari (gün -> [(tip, saat, dk, sure_dk)])
ANOMALY_DAYS: dict[int, list[tuple[str, int, int, int]]] = {
    5: [("early_tea", 9, 15, 12), ("extended_meal", 12, 0, 52)],  # 5 May
    12: [("extended_meal", 20, 0, 58)],  # 12 May yemek uzadi
    18: [("early_tea", 15, 20, 12), ("early_tea", 21, 15, 12)],  # erken cay
    22: [("extended_meal", 12, 0, 48)],
}


def _ensure_molds(db, machine_id: int) -> dict[str, Mold]:
    out: dict[str, Mold] = {}
    for key, name in MOLDS.items():
        m = db.query(Mold).filter(Mold.name == name).first()
        if not m:
            m = Mold(name=name, status="active", avg_cycle_s=10.5, tolerance_s=1.2, sample_count=0, confidence=0.9)
            db.add(m)
            db.flush()
        link = (
            db.query(MoldMachine)
            .filter(MoldMachine.machine_id == machine_id, MoldMachine.mold_id == m.id)
            .first()
        )
        if not link:
            db.add(MoldMachine(machine_id=machine_id, mold_id=m.id))
        out[key] = m
    db.commit()
    return out


def _planned_segments() -> list[tuple[datetime, datetime, str | None]]:
    """Return (start, end, mold_key|None) covering production plan until END or plan exhausted."""
    segs: list[tuple[datetime, datetime, str | None]] = []
    t = START
    for mold_key, dur in PRODUCTION_PLAN:
        if t >= END:
            break
        end = min(END, t + dur)
        segs.append((t, end, mold_key))
        t = end
    # Pad remainder with alternating k2/k1 runs if month not full
    toggle = True
    while t < END - timedelta(hours=6):
        mk = "k2" if toggle else "k1"
        run = timedelta(days=2 if toggle else 1)
        segs.append((t, min(END, t + run), mk))
        t = min(END, t + run)
        if t >= END:
            break
        segs.append((t, min(END, t + timedelta(minutes=random.randint(25, 55))), None))
        t = segs[-1][1]
        toggle = not toggle
    return segs


def _break_windows_for_day(day: datetime.date) -> list[tuple[datetime, datetime]]:
    """Makine durur: molada döngü üretilmez (olay kaydı yok, grafikte boşluk)."""
    windows: list[tuple[datetime, datetime]] = []
    d0 = datetime(day.year, day.month, day.day, tzinfo=TZ)

    for h, m, mins in DAILY_MEALS:
        start = d0 + timedelta(hours=h, minutes=m)
        duration = mins
        for ad, ah, am, extra in ANOMALY_DAYS.get(day.day, []):
            if ad == "extended_meal" and ah == h and am == m:
                duration = extra
        windows.append((start, start + timedelta(minutes=duration)))

    for h, m, mins in DAILY_TEAS:
        start = d0 + timedelta(hours=h, minutes=m)
        duration = mins
        for ad, ah, am, _extra in ANOMALY_DAYS.get(day.day, []):
            if ad == "early_tea" and ah == h and am == m:
                start = start - timedelta(minutes=8)
        windows.append((start, start + timedelta(minutes=duration)))

    return sorted(windows, key=lambda x: x[0])


def _pause_windows_for(t: datetime, day_breaks: dict[datetime.date, list[tuple[datetime, datetime]]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for d in (t.date(), (t - timedelta(days=1)).date(), (t + timedelta(days=1)).date()):
        out.extend(day_breaks.get(d, []))
    return out


def _advance_past_pauses(t: datetime, day_breaks: dict[datetime.date, list[tuple[datetime, datetime]]]) -> datetime:
    while True:
        moved = False
        for a, b in _pause_windows_for(t, day_breaks):
            if a <= t < b:
                t = b
                moved = True
        if not moved:
            return t


def _overlaps_pause(t0: datetime, t1: datetime, day_breaks: dict[datetime.date, list[tuple[datetime, datetime]]]) -> bool:
    for a, b in _pause_windows_for(t0, day_breaks):
        if t0 < b and t1 > a:
            return True
    return False


def _next_pause_start_after(t: datetime, until: datetime, day_breaks: dict) -> datetime | None:
    nxt: datetime | None = None
    for a, _b in _pause_windows_for(t, day_breaks):
        if t < a < until:
            if nxt is None or a < nxt:
                nxt = a
    return nxt


def _emit_cycles(
    rng: random.Random,
    mold: Mold,
    run_start: datetime,
    run_end: datetime,
    day_breaks: dict[datetime.date, list[tuple[datetime, datetime]]],
    batch: list[Cycle],
) -> None:
    t = _advance_past_pauses(run_start, day_breaks)
    mold_name = mold.name or "—"
    base = float(mold.avg_cycle_s or 10.5)
    while t < run_end:
        t = _advance_past_pauses(t, day_breaks)
        if t >= run_end:
            break

        dur = max(4.5, rng.gauss(base, 1.1))
        if rng.random() < 0.03:
            dur += rng.uniform(5, 12)
        t_end = t + timedelta(seconds=dur)
        if t_end > run_end:
            break

        pause_at = _next_pause_start_after(t, min(run_end, t + timedelta(hours=6)), day_breaks)
        if pause_at is not None and t_end > pause_at:
            t = pause_at
            continue

        if _overlaps_pause(t, t_end, day_breaks):
            t = _advance_past_pauses(t, day_breaks)
            continue

        batch.append(
            Cycle(
                machine_id=MACHINE_ID,
                mold_id=mold.id,
                cycle_time_s=round(dur, 3),
                t_start=t,
                t_end=t_end,
                confidence=round(rng.uniform(0.8, 0.98), 3),
                mold_name_snapshot=mold_name,
                is_counted=True,
            )
        )
        gap = rng.expovariate(1.0 / 1.8)
        t = _advance_past_pauses(t_end + timedelta(seconds=max(0.4, gap)), day_breaks)


def seed() -> dict:
    db_session.get_engine()
    db_session.init_db()
    db = db_session.SessionLocal()
    rng = random.Random(20260501)
    try:
        machine = db.get(Machine, MACHINE_ID)
        if not machine:
            raise SystemExit(f"Machine {MACHINE_ID} not found")

        deleted_c = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).delete(synchronize_session=False)
        deleted_e = db.query(Event).filter(Event.machine_id == MACHINE_ID).delete(synchronize_session=False)
        db.commit()

        molds = _ensure_molds(db, MACHINE_ID)
        prod_segs = _planned_segments()

        day_breaks: dict[datetime.date, list] = {}
        d = START.date()
        while d < END.date():
            day_breaks[d] = _break_windows_for_day(d)
            d += timedelta(days=1)

        batch: list[Cycle] = []

        for run_start, run_end, mold_key in prod_segs:
            if mold_key is None:
                continue
            _emit_cycles(rng, molds[mold_key], run_start, run_end, day_breaks, batch)
            if len(batch) >= 800:
                db.add_all(batch)
                db.commit()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.commit()

        machine.current_mold_id = molds["k2"].id
        for m in molds.values():
            cnt = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID, Cycle.mold_id == m.id).count()
            m.sample_count = cnt
        db.commit()

        total = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).count()
        return {
            "machine_id": MACHINE_ID,
            "deleted_cycles": int(deleted_c),
            "deleted_events": int(deleted_e),
            "inserted_cycles": total,
            "events_added": 0,
            "period": f"{START.isoformat()} .. {END.isoformat()}",
        }
    finally:
        db.close()


if __name__ == "__main__":
    print(seed())
