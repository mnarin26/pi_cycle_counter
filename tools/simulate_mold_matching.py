#!/usr/bin/env python3
"""Simulate automatic mold matching via real handle_cycle_completion()."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.db.session as db_session  # noqa: E402
from app.db.models import Cycle, Event, Machine, Mold, MoldMachine  # noqa: E402
from app.services.mold_matcher import (  # noqa: E402
    LONG_STOP_MOLD_CHANGE_S,
    POST_STOP_REF_CYCLES,
    _post_stop_buffers,
    _post_stop_window_size,
    handle_cycle_completion,
)

MACHINE_ID = 3
TZ = timezone.utc
MOLD_SPECS = (
    ("Kalip-3A", 9.8, 1.1),
    ("Kalip-3B", 11.6, 1.1),
    ("Kalip-3C", 13.2, 1.2),
)


def _ensure_molds(db) -> dict[str, Mold]:
    out: dict[str, Mold] = {}
    for name, avg, tol in MOLD_SPECS:
        m = db.query(Mold).filter(Mold.name == name).first()
        if not m:
            m = Mold(
                name=name,
                status="active",
                avg_cycle_s=avg,
                tolerance_s=tol,
                sample_count=10,
                confidence=0.9,
            )
            db.add(m)
            db.flush()
        else:
            m.status = "active"
            m.avg_cycle_s = avg
            m.tolerance_s = tol
        link = (
            db.query(MoldMachine)
            .filter(MoldMachine.machine_id == MACHINE_ID, MoldMachine.mold_id == m.id)
            .first()
        )
        if not link:
            db.add(MoldMachine(machine_id=MACHINE_ID, mold_id=m.id, cycles_attributed=0))
        out[name] = m
    db.commit()
    return out


def _reset_machine_history(db) -> None:
    db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).delete(synchronize_session=False)
    db.query(Event).filter(Event.machine_id == MACHINE_ID).delete(synchronize_session=False)
    db.commit()


def _insert_counted_cycle(db, mold: Mold, cycle_s: float, t_end: datetime) -> None:
    t_start = t_end - timedelta(seconds=cycle_s)
    db.add(
        Cycle(
            machine_id=MACHINE_ID,
            mold_id=mold.id,
            cycle_time_s=cycle_s,
            t_start=t_start,
            t_end=t_end,
            confidence=0.95,
            mold_name_snapshot=mold.name,
            is_counted=True,
        )
    )
    db.commit()


def _emit(db, machine: Machine, cycle_s: float, t_end: datetime, rolling: dict) -> dict:
    t_start = t_end - timedelta(seconds=cycle_s)
    handle_cycle_completion(
        db,
        machine,
        cycle_s,
        t_start,
        t_end,
        rolling,
        None,
        0.95,
    )
    db.refresh(machine)
    last_ev = (
        db.query(Event)
        .filter(Event.machine_id == MACHINE_ID)
        .order_by(Event.created_at.desc())
        .first()
    )
    last_cy = (
        db.query(Cycle)
        .filter(Cycle.machine_id == MACHINE_ID)
        .order_by(Cycle.t_end.desc())
        .first()
    )
    mold_name = None
    if machine.current_mold_id:
        m = db.get(Mold, machine.current_mold_id)
        mold_name = m.name if m else None
    return {
        "cycle_s": cycle_s,
        "current_mold_id": machine.current_mold_id,
        "current_mold_name": mold_name,
        "last_event_type": last_ev.type if last_ev else None,
        "last_cycle_mold": last_cy.mold_name_snapshot if last_cy else None,
        "last_cycle_counted": last_cy.is_counted if last_cy else None,
    }


def _emit_post_stop_sequence(
    db,
    machine: Machine,
    rolling: dict,
    *,
    prev_mold: Mold,
    decision_cycle_s: float,
    gap_min: int,
    warmup_cycle_s: float | None = None,
) -> dict:
    """After long stop: emit cycles until adaptive post-stop matcher decides."""
    now = datetime.now(TZ)
    _insert_counted_cycle(db, prev_mold, prev_mold.avg_cycle_s, now - timedelta(minutes=gap_min))
    _post_stop_buffers.pop(MACHINE_ID, None)
    rolling.clear()
    machine.current_mold_id = prev_mold.id
    db.commit()

    window = _post_stop_window_size(decision_cycle_s)
    total = max(POST_STOP_REF_CYCLES + window + 5, window + 15)
    t = now - timedelta(minutes=5)
    result: dict = {}
    for i in range(total):
        base = warmup_cycle_s if warmup_cycle_s is not None and i < POST_STOP_REF_CYCLES else decision_cycle_s
        jitter = (i % 3 - 1) * 0.04
        cycle_s = base + jitter
        t = t + timedelta(seconds=cycle_s)
        result = _emit(db, machine, cycle_s, t, rolling)
        if result.get("last_event_type") in (
            "mold_auto_matched",
            "mold_change_likely",
            "mold_suggestion",
            "mold_unknown_prompt",
        ):
            break
    return result


def _print_scenario(title: str, result: dict, expected: str) -> None:
    print(f"\n=== {title} ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print(f"  beklenen: {expected}")


def simulate() -> None:
    db_session.get_engine()
    db = db_session.SessionLocal()
    rolling: dict[int, list[float]] = {}
    try:
        machine = db.get(Machine, MACHINE_ID)
        if not machine:
            raise SystemExit(f"Machine {MACHINE_ID} not found")

        molds = _ensure_molds(db)
        ma, mb, mc = molds["Kalip-3A"], molds["Kalip-3B"], molds["Kalip-3C"]
        gap_min = int(LONG_STOP_MOLD_CHANGE_S / 60) + 5

        print(f"Makine #{MACHINE_ID} — otomatik kalıp eşleştirme simülasyonu")
        print(f"Uzun duruş eşiği: {LONG_STOP_MOLD_CHANGE_S}s ({gap_min} dk+)")

        # --- Senaryo 1: Kalip-3A aktif, uzun duruş, 21–40. döngü Kalip-3B ---
        _reset_machine_history(db)
        rolling.clear()
        _post_stop_buffers.pop(MACHINE_ID, None)
        machine.current_mold_id = ma.id
        db.commit()

        r1 = _emit_post_stop_sequence(
            db, machine, rolling,
            prev_mold=ma,
            decision_cycle_s=mb.avg_cycle_s,
            gap_min=gap_min,
            warmup_cycle_s=9.0,
        )
        _print_scenario(
            "1) Uzun duruş sonrası stabil pencere ile otomatik eşleşme (3A → 3B)",
            r1,
            f"current_mold={mb.name}, event=mold_auto_matched",
        )

        # --- Senaryo 2: 21–40. döngü kayıtlı kalıpla eşleşmiyor ---
        _reset_machine_history(db)
        rolling.clear()
        _post_stop_buffers.pop(MACHINE_ID, None)
        machine.current_mold_id = ma.id
        db.commit()

        r2 = _emit_post_stop_sequence(
            db, machine, rolling,
            prev_mold=ma,
            decision_cycle_s=25.5,
            gap_min=gap_min,
        )
        _print_scenario(
            "2) Uzun duruş, stabil pencere hiçbir kayıtlı kalıpla eşleşmiyor",
            r2,
            "current_mold=None, event=mold_change_likely",
        )

        # --- Senaryo 3: Aktif kalıp yok, 5 stabil döngü → Kalip-3C ---
        _reset_machine_history(db)
        rolling.clear()
        _post_stop_buffers.pop(MACHINE_ID, None)
        machine.current_mold_id = None
        db.commit()

        t = datetime.now(TZ) - timedelta(minutes=2)
        r3 = {}
        for i in range(5):
            t = t + timedelta(seconds=13.2)
            r3 = _emit(db, machine, 13.15 + (i % 2) * 0.05, t, rolling)
        _print_scenario(
            "3) Aktif kalıp yok, 5 döngü sonrası kayıtlı kalıp eşleşmesi (3C)",
            r3,
            f"current_mold={mc.name}, event=mold_auto_matched veya update",
        )

        # --- Senaryo 4: Eşleşme yok, 5 döngü → yeni kalıp önerisi ---
        _reset_machine_history(db)
        rolling.clear()
        _post_stop_buffers.pop(MACHINE_ID, None)
        machine.current_mold_id = None
        db.commit()

        t = datetime.now(TZ) - timedelta(minutes=2)
        r4 = {}
        for i in range(5):
            t = t + timedelta(seconds=7.4)
            r4 = _emit(db, machine, 7.35 + (i % 2) * 0.03, t, rolling)
        _print_scenario(
            "4) Bilinmeyen süre profili → yeni kalıp önerisi",
            r4,
            "event=mold_suggestion, isimsiz candidate",
        )

        total_cycles = db.query(Cycle).filter(Cycle.machine_id == MACHINE_ID).count()
        print(f"\nSimülasyon sonrası makine #{MACHINE_ID} cycle sayısı: {total_cycles}")
        print("(Sadece simülasyon verisi kaldı; canlı seed için seed_machine3_three_shifts.py tekrar çalıştırılabilir.)")
    finally:
        db.close()


if __name__ == "__main__":
    simulate()
