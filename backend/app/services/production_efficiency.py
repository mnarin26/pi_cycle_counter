"""Target-vs-actual production efficiency, sliced by mold changes.

Core model (single formula for every window):

    gereken (expected) = calisma_suresi / efektif_cevrim
    verimlilik         = gerceklesen / gereken

`efektif_cevrim` is either the mold's target cycle, or - when a daily print
target is set (daily-first) - derived from it:

    efektif_cevrim = gunluk_calisma_saniyesi / gunluk_hedef

`calisma_suresi` subtracts non-productive time that has *already passed*:
  - manual molds: shift break windows (12:00-12:30 ...), once fully over
  - every mold change: previous mold removal + new mold mount time, once over

Non-productive time is only deducted after it has fully elapsed, so efficiency
does not look artificially high mid-break.

Operator-entered downtime works the opposite way: its cycles are not counted and
its time is *not* removed from the denominator, so efficiency drops. When downtime
overlaps a break or a mold change, those excused portions win (mola > değişim >
duruş) and only the remaining downtime hurts efficiency.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Machine, MachineDowntime, Mold
from app.services.shifts import (
    ShiftDef,
    current_shift,
    get_shift_defs,
    shift_break_windows,
    shift_duration_hours,
    shift_window,
)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _count_cycles(db: Session, machine_id: int, start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    return int(
        db.query(func.count(Cycle.id))
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .scalar()
        or 0
    )


def _avg_cycle_s(
    db: Session, machine_id: int, start: datetime, end: datetime, mold_id: int | None = None
) -> float:
    if end <= start:
        return 0.0
    q = db.query(func.avg(Cycle.cycle_time_s)).filter(
        Cycle.machine_id == machine_id,
        Cycle.t_end >= start,
        Cycle.t_end <= end,
        Cycle.is_counted.is_(True),
    )
    if mold_id is not None:
        q = q.filter(Cycle.mold_id == mold_id)
    avg = q.scalar()
    return round(float(avg or 0.0), 2)


def _mold_assignment_events(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[tuple[datetime, int | None]]:
    rows = (
        db.query(Event.created_at, Event.payload)
        .filter(
            Event.machine_id == machine_id,
            Event.type == "mold_assigned",
            Event.created_at > start,
            Event.created_at <= end,
        )
        .order_by(Event.created_at)
        .all()
    )
    out: list[tuple[datetime, int | None]] = []
    for created_at, payload in rows:
        mid: int | None = None
        if payload:
            try:
                mid = json.loads(payload).get("mold_id")
            except (ValueError, TypeError):
                mid = None
        out.append((_as_utc(created_at), mid))
    return out


def _mold_at(db: Session, machine_id: int, when: datetime) -> int | None:
    """Mold assigned as of `when` (latest mold_assigned at or before)."""
    row = (
        db.query(Event.payload)
        .filter(
            Event.machine_id == machine_id,
            Event.type == "mold_assigned",
            Event.created_at <= when,
        )
        .order_by(Event.created_at.desc())
        .limit(1)
        .first()
    )
    if row and row[0]:
        try:
            mid = json.loads(row[0]).get("mold_id")
            if mid is not None:
                return int(mid)
        except (ValueError, TypeError):
            pass
    machine = db.get(Machine, machine_id)
    return machine.current_mold_id if machine else None


def mold_target_cycle_s(db: Session, mold_id: int | None) -> float | None:
    """Real target cycle for display (target_cycle_s, else learned avg)."""
    if not mold_id:
        return None
    mold = db.get(Mold, mold_id)
    if not mold:
        return None
    if mold.target_cycle_s and mold.target_cycle_s > 0:
        return float(mold.target_cycle_s)
    if mold.avg_cycle_s and mold.avg_cycle_s > 0:
        return float(mold.avg_cycle_s)
    return None


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _day_seconds_and_breaks(shift_defs: list[ShiftDef]) -> tuple[float, float]:
    total = 0.0
    breaks = 0.0
    for sh in shift_defs:
        total += shift_duration_hours(sh) * 3600.0
        for bs, be in sh.breaks:
            d = _minutes(be) - _minutes(bs)
            if d < 0:
                d += 24 * 60
            breaks += d * 60.0
    return total, breaks


def effective_cycle_s(db: Session, mold_id: int | None, shift_defs: list[ShiftDef]) -> float | None:
    """Effective cycle used in the formula. Daily target takes priority."""
    if not mold_id:
        return None
    mold = db.get(Mold, mold_id)
    if not mold:
        return None
    daily = mold.daily_target_count
    if daily and daily > 0:
        total, brk = _day_seconds_and_breaks(shift_defs)
        working = total - (brk if (mold.work_mode == "manual") else 0.0)
        if working > 0:
            return working / float(daily)
    if mold.target_cycle_s and mold.target_cycle_s > 0:
        return float(mold.target_cycle_s)
    if mold.avg_cycle_s and mold.avg_cycle_s > 0:
        return float(mold.avg_cycle_s)
    return None


def _changeover_seconds(db: Session, machine_id: int, at: datetime) -> float:
    """(previous mold removal + new mold mount) minutes -> seconds, at an assignment."""
    new_mold_id = _mold_at(db, machine_id, at)
    prev_mold_id = _mold_at(db, machine_id, at - timedelta(seconds=1))
    seconds = 0.0
    if prev_mold_id and prev_mold_id != new_mold_id:
        prev = db.get(Mold, prev_mold_id)
        if prev and prev.removal_minutes:
            seconds += float(prev.removal_minutes) * 60.0
    if new_mold_id:
        new = db.get(Mold, new_mold_id)
        if new and new.mount_minutes:
            seconds += float(new.mount_minutes) * 60.0
    return seconds


Interval = tuple[datetime, datetime]


def _clip(intervals: list[Interval], a: datetime, e: datetime) -> list[Interval]:
    out: list[Interval] = []
    for s, x in intervals:
        lo = max(s, a)
        hi = min(x, e)
        if hi > lo:
            out.append((lo, hi))
    return out


def _merge(intervals: list[Interval]) -> list[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    out: list[Interval] = [ordered[0]]
    for s, x in ordered[1:]:
        last_s, last_e = out[-1]
        if s <= last_e:
            out[-1] = (last_s, max(last_e, x))
        else:
            out.append((s, x))
    return out


def _subtract(base: list[Interval], cut: list[Interval]) -> list[Interval]:
    """base minus cut (both lists of intervals)."""
    cuts = _merge(cut)
    result: list[Interval] = []
    for s, x in base:
        segs: list[Interval] = [(s, x)]
        for cs, ce in cuts:
            nxt: list[Interval] = []
            for bs, be in segs:
                if ce <= bs or cs >= be:
                    nxt.append((bs, be))
                    continue
                if cs > bs:
                    nxt.append((bs, cs))
                if ce < be:
                    nxt.append((ce, be))
            segs = nxt
        result.extend(segs)
    return result


def _total_seconds(intervals: list[Interval]) -> float:
    return sum((x - s).total_seconds() for s, x in intervals)


def _changeover_windows(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[Interval]:
    """Changeover intervals (removal+mount) that overlap (start, end].

    Window is explicit when the assignment payload carries changeover_start/end,
    otherwise the standard removal+mount duration ending at the assignment time.
    """
    rows = (
        db.query(Event.created_at, Event.payload)
        .filter(
            Event.machine_id == machine_id,
            Event.type == "mold_assigned",
            Event.created_at > start,
            Event.created_at <= end,
        )
        .order_by(Event.created_at)
        .all()
    )
    windows: list[Interval] = []
    for created_at, payload in rows:
        at = _as_utc(created_at)
        cs = ce = None
        if payload:
            try:
                data = json.loads(payload)
                cs_raw = data.get("changeover_start")
                ce_raw = data.get("changeover_end")
                if cs_raw and ce_raw:
                    cs = _as_utc(datetime.fromisoformat(cs_raw))
                    ce = _as_utc(datetime.fromisoformat(ce_raw))
            except (ValueError, TypeError):
                cs = ce = None
        if cs is None or ce is None or ce <= cs:
            co = _changeover_seconds(db, machine_id, at)
            if co <= 0:
                continue
            cs, ce = at - timedelta(seconds=co), at
        windows.append((cs, ce))
    return windows


def _downtime_windows(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[Interval]:
    rows = (
        db.query(MachineDowntime.start_at, MachineDowntime.end_at)
        .filter(
            MachineDowntime.machine_id == machine_id,
            MachineDowntime.end_at > start,
            MachineDowntime.start_at < end,
        )
        .all()
    )
    return [(_as_utc(s), _as_utc(x)) for s, x in rows]


def _block(
    actual: int, expected: float, avg_cycle_s: float, target_cycle_s: float | None, available: bool
) -> dict:
    eff = round(100.0 * actual / expected, 1) if (available and expected > 0) else None
    return {
        "actual_count": actual,
        "target_count": int(round(expected)),
        "efficiency_pct": eff,
        "avg_cycle_s": avg_cycle_s,
        "target_cycle_s": target_cycle_s,
        "available": available,
    }


def _compute(
    db: Session,
    machine_id: int,
    shift: ShiftDef,
    sw_start: datetime,
    sw_end: datetime,
    seg_start: datetime,
    cap: datetime,
) -> tuple[int, float, bool, int | None]:
    """Return (actual, expected, available, last_mold_id) over [seg_start, cap]."""
    shift_defs = get_shift_defs(db)
    if cap <= seg_start:
        m0 = _mold_at(db, machine_id, seg_start)
        return 0, 0.0, effective_cycle_s(db, m0, shift_defs) is not None, m0

    events = _mold_assignment_events(db, machine_id, sw_start, cap)
    event_times = [t for (t, _mid) in events if seg_start <= t < cap]
    boundaries = [seg_start] + [t for t in event_times if t > seg_start] + [cap]
    breaks = shift_break_windows(shift, sw_start, sw_end)
    co_windows = _changeover_windows(db, machine_id, seg_start, cap)
    dt_windows = _downtime_windows(db, machine_id, seg_start, cap)

    total_actual = 0
    total_expected = 0.0
    available = False
    last_mold: int | None = None
    for i in range(len(boundaries) - 1):
        a = boundaries[i]
        b = boundaries[i + 1]
        e = min(b, cap)
        if e <= a:
            continue
        mid = _mold_at(db, machine_id, a)
        last_mold = mid
        eff_c = effective_cycle_s(db, mid, shift_defs)
        if eff_c:
            available = True

        raw_s = (e - a).total_seconds()

        # Forgiven (non-productive but excused) time = breaks + changeover.
        # Priority: mola > kalıp değişimi > duruş. Forgiven time is deducted from
        # the target denominator; downtime is NOT (so efficiency drops).
        forgiven: list[Interval] = []
        mold = db.get(Mold, mid) if mid else None
        if mold and mold.work_mode == "manual":
            for bs, be in breaks:
                if be <= e:  # only fully-passed breaks
                    lo, hi = max(bs, a), min(be, e)
                    if hi > lo:
                        forgiven.append((lo, hi))
        for cs, ce in co_windows:
            if ce <= e:  # only fully-passed changeover
                lo, hi = max(cs, a), min(ce, e)
                if hi > lo:
                    forgiven.append((lo, hi))
        forgiven = _merge(forgiven)
        nonprod = _total_seconds(forgiven)

        # Downtime not already excused as break/changeover hurts efficiency: its
        # cycles are not counted and its time stays in the denominator.
        hurt = _merge(_subtract(_clip(dt_windows, a, e), forgiven))

        working = max(0.0, raw_s - nonprod)
        seg_actual = _count_cycles(db, machine_id, a, e)
        for hs, he in hurt:
            seg_actual -= _count_cycles(db, machine_id, hs, he)
        total_actual += max(0, seg_actual)
        if eff_c:
            total_expected += working / eff_c

    return total_actual, total_expected, available, last_mold


def active_mold_start(
    db: Session, machine_id: int, active_mold_id: int | None, win_start: datetime, cap: datetime
) -> datetime:
    """When the active mold's run began within the window (assignment time or win start)."""
    if not active_mold_id:
        return win_start
    for created_at, mid in reversed(_mold_assignment_events(db, machine_id, win_start, cap)):
        if mid == active_mold_id:
            return created_at
    return win_start


def segment_efficiency(
    db: Session,
    machine_id: int,
    shift: ShiftDef,
    sw_start: datetime,
    sw_end: datetime,
    seg_start: datetime,
    seg_end: datetime,
    now: datetime,
    mold_id: int | None = None,
) -> dict:
    cap = min(seg_end, now)
    avg_end = cap if cap > seg_start else seg_end
    avg = _avg_cycle_s(db, machine_id, seg_start, avg_end, mold_id)
    actual, expected, available, last_mold = _compute(
        db, machine_id, shift, sw_start, sw_end, seg_start, cap
    )
    return _block(actual, expected, avg, mold_target_cycle_s(db, last_mold), available)


def shift_efficiency(
    db: Session, machine_id: int, shift: ShiftDef, sw_start: datetime, sw_end: datetime, now: datetime
) -> dict:
    return segment_efficiency(db, machine_id, shift, sw_start, sw_end, sw_start, sw_end, now)


def shift_target_plan(
    db: Session, machine_id: int, shift: ShiftDef, sw_start: datetime, sw_end: datetime, now: datetime
) -> tuple[int, bool]:
    """Planned shift target for the whole shift (fixed, not capped at now)."""
    _actual, expected, available, _last = _compute(
        db, machine_id, shift, sw_start, sw_end, sw_start, sw_end
    )
    return int(round(expected)), available


def changeover_windows(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[dict]:
    """Public changeover intervals clipped to [start, end] for chart overlays."""
    out = []
    for cs, ce in _clip(_changeover_windows(db, machine_id, start, end), _as_utc(start), _as_utc(end)):
        out.append({"start": cs.isoformat(), "end": ce.isoformat()})
    return out


def downtime_windows(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[dict]:
    """Public downtime intervals clipped to [start, end] for chart overlays."""
    out = []
    for ds, de in _clip(_downtime_windows(db, machine_id, start, end), _as_utc(start), _as_utc(end)):
        out.append({"start": ds.isoformat(), "end": de.isoformat()})
    return out


def hurt_downtime_windows(
    db: Session, machine_id: int, start: datetime, end: datetime
) -> list[Interval]:
    """Downtime that lowers efficiency in [start, end]: downtime − changeover − breaks.

    Used by TV charts / summaries so excluded cycles match the efficiency engine.
    Breaks are subtracted for all molds here (manual-only nuance is applied inside
    `_compute` for the denominator; excluding those minutes from chart counts is safe).
    """
    start_u = _as_utc(start)
    end_u = _as_utc(end)
    if not start_u or not end_u or end_u <= start_u:
        return []
    dts = _clip(_downtime_windows(db, machine_id, start_u, end_u), start_u, end_u)
    if not dts:
        return []
    # Look back so changeovers that end at an assignment near the window start are included.
    lookback = start_u - timedelta(hours=6)
    forgiven: list[Interval] = list(
        _clip(_changeover_windows(db, machine_id, lookback, end_u), start_u, end_u)
    )
    cursor = start_u
    guard = 0
    while cursor < end_u and guard < 400:
        guard += 1
        sh = current_shift(db, cursor)
        sw_start, sw_end = shift_window(sh, cursor, cap_now=False)
        for bs, be in shift_break_windows(sh, sw_start, sw_end):
            lo, hi = max(bs, start_u), min(be, end_u)
            if hi > lo:
                forgiven.append((lo, hi))
        cursor = sw_end + timedelta(seconds=1)
    return _merge(_subtract(dts, _merge(forgiven)))


def range_efficiency(db: Session, machine_id: int, start: datetime, end: datetime, now: datetime) -> dict:
    """Efficiency over an arbitrary [start, end], summed across shift instances."""
    total_actual = 0
    total_expected = 0.0
    available = False
    last_mold: int | None = None
    cursor = start
    guard = 0
    while cursor < end and guard < 400:
        guard += 1
        sh = current_shift(db, cursor)
        sw_start, sw_end = shift_window(sh, cursor, cap_now=False)
        seg_start = max(sw_start, start)
        seg_end = min(sw_end, end)
        cap = min(seg_end, now)
        if cap > seg_start:
            a, exp, avail, lm = _compute(db, machine_id, sh, sw_start, sw_end, seg_start, cap)
            total_actual += a
            total_expected += exp
            available = available or avail
            if lm is not None:
                last_mold = lm
        cursor = sw_end + timedelta(seconds=1)
    avg = _avg_cycle_s(db, machine_id, start, min(end, now))
    return _block(total_actual, total_expected, avg, mold_target_cycle_s(db, last_mold), available)
