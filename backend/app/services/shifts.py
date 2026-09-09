"""Shift windows for production reporting (Europe/Istanbul)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.services.production_settings import DEFAULT_SHIFTS, get_production_settings
from app.services.time_windows import DISPLAY_TZ, ensure_utc

DEFAULT_SHIFT_DEFS = DEFAULT_SHIFTS


@dataclass(frozen=True)
class ShiftDef:
    id: str
    name: str
    start: time
    end: time
    breaks: tuple[tuple[time, time], ...] = ()


def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    if hour == 24 and minute == 0:
        return time(23, 59, 59, 999999)
    return time(hour, minute)


def _normalize_shift(raw: dict) -> ShiftDef | None:
    sid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    start_s = str(raw.get("start") or "").strip()
    end_s = str(raw.get("end") or "").strip()
    if not sid or not name or not start_s or not end_s:
        return None
    try:
        start = _parse_hhmm(start_s)
        end = _parse_hhmm(end_s)
    except (TypeError, ValueError):
        return None
    breaks: list[tuple[time, time]] = []
    raw_breaks = raw.get("breaks")
    if isinstance(raw_breaks, list):
        for item in raw_breaks:
            if not isinstance(item, dict):
                continue
            bs = str(item.get("start") or "").strip()
            be = str(item.get("end") or "").strip()
            if not bs or not be:
                continue
            try:
                breaks.append((_parse_hhmm(bs), _parse_hhmm(be)))
            except (TypeError, ValueError):
                continue
    return ShiftDef(id=sid, name=name, start=start, end=end, breaks=tuple(breaks))


def get_shift_defs(db: Session | None = None) -> list[ShiftDef]:
    raw_shifts = DEFAULT_SHIFTS
    if db is not None:
        raw_shifts = get_production_settings(db)["shifts"]
    out: list[ShiftDef] = []
    for item in raw_shifts:
        if not isinstance(item, dict):
            continue
        shift = _normalize_shift(item)
        if shift:
            out.append(shift)
    return out or [_normalize_shift(s) for s in DEFAULT_SHIFTS if _normalize_shift(s)]


def _local_dt(day, t: time) -> datetime:
    return datetime(day.year, day.month, day.day, t.hour, t.minute, t.second, t.microsecond, tzinfo=DISPLAY_TZ)


def _exclusive_end_dt(day, t: time) -> datetime:
    """Treat HH:59 as inclusive last minute (07:00 next hour); HH:00 as exclusive."""
    if t.hour == 23 and t.minute >= 59:
        return _local_dt(day, time(0, 0)) + timedelta(days=1)
    if t.minute == 59:
        return _local_dt(day, time(t.hour, 0)) + timedelta(hours=1)
    return _local_dt(day, t.replace(second=0, microsecond=0))


def format_shift_clock(t: time, *, is_end: bool = False) -> str:
    if is_end and t.minute >= 59:
        if t.hour == 23:
            return "24:00"
        return f"{(t.hour + 1) % 24:02d}:00"
    return f"{t.hour:02d}:{t.minute:02d}"


def shift_window(
    shift: ShiftDef,
    at: datetime | None = None,
    *,
    cap_now: bool = True,
) -> tuple[datetime, datetime]:
    """Return UTC bounds for the shift instance containing `at` (Istanbul wall clock)."""
    now_utc = ensure_utc(at or datetime.now(timezone.utc))
    local = now_utc.astimezone(DISPLAY_TZ)
    day = local.date()
    start_local = _local_dt(day, shift.start.replace(second=0, microsecond=0))
    end_local = _exclusive_end_dt(day, shift.end)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    if local < start_local:
        start_local -= timedelta(days=1)
        end_local -= timedelta(days=1)
    elif local >= end_local:
        start_local += timedelta(days=1)
        end_local += timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    if cap_now:
        end_utc = min(end_utc, now_utc)
    return start_utc, end_utc


def shift_break_windows(
    shift: ShiftDef, win_start: datetime, win_end: datetime
) -> list[tuple[datetime, datetime]]:
    """UTC break intervals for this shift instance that overlap [win_start, win_end].

    Break clock times (HH:MM) are placed onto the shift instance's local day,
    handling shifts that cross midnight. Ends are returned unclamped so callers
    can apply the 'only after fully passed' rule.
    """
    if not shift.breaks:
        return []
    ws = ensure_utc(win_start)
    we = ensure_utc(win_end)
    start_local = ws.astimezone(DISPLAY_TZ)
    anchor_day = start_local.date()
    out: list[tuple[datetime, datetime]] = []
    for bs, be in shift.breaks:
        b_start = _local_dt(anchor_day, bs)
        b_end = _local_dt(anchor_day, be)
        if b_start < start_local:
            b_start += timedelta(days=1)
            b_end += timedelta(days=1)
        if b_end <= b_start:
            b_end += timedelta(days=1)
        bs_utc = b_start.astimezone(timezone.utc)
        be_utc = b_end.astimezone(timezone.utc)
        if be_utc > ws and bs_utc < we:  # overlaps the shift window
            out.append((bs_utc, be_utc))
    return out


def shift_hour_slots(start_utc: datetime, end_utc: datetime) -> list[int]:
    """Istanbul hours from shift start (inclusive) to end (exclusive), in order."""
    start_local = ensure_utc(start_utc).astimezone(DISPLAY_TZ)
    end_local = ensure_utc(end_utc).astimezone(DISPLAY_TZ)
    t = start_local.replace(minute=0, second=0, microsecond=0)
    slots: list[int] = []
    while t < end_local and len(slots) < 24:
        slots.append(t.hour)
        t += timedelta(hours=1)
    return slots


def recent_shift_windows(
    db: Session | None = None,
    at: datetime | None = None,
) -> list[tuple[ShiftDef, datetime, datetime]]:
    """Current shift first, then previous instances. Length = configured shift count."""
    now_utc = ensure_utc(at or datetime.now(timezone.utc))
    defs = get_shift_defs(db)
    if not defs:
        return []
    out: list[tuple[ShiftDef, datetime, datetime]] = []
    cursor = now_utc
    for _ in range(len(defs)):
        sh = current_shift(db, cursor)
        start, end = shift_window(sh, cursor, cap_now=False)
        out.append((sh, start, end))
        cursor = start - timedelta(seconds=1)
    return out


def current_shift(db: Session | None = None, at: datetime | None = None) -> ShiftDef:
    now_utc = ensure_utc(at or datetime.now(timezone.utc))
    local = now_utc.astimezone(DISPLAY_TZ)
    t = local.time().replace(second=0, microsecond=0)
    defs = get_shift_defs(db)
    for shift in defs:
        start = shift.start.replace(second=0, microsecond=0)
        end = shift.end.replace(second=0, microsecond=0)
        if end > start:
            if start <= t <= end:
                return shift
        else:
            if t >= start or t <= end:
                return shift
    return defs[0]


def shift_duration_hours(shift: ShiftDef) -> float:
    start_m = shift.start.hour * 60 + shift.start.minute
    end_m = shift.end.hour * 60 + shift.end.minute
    if end_m <= start_m:
        end_m += 24 * 60
    return max(1.0, (end_m - start_m) / 60.0)


def daily_target_for_shift(daily_target: int | None, shift: ShiftDef, db: Session | None = None) -> int:
    if not daily_target or daily_target <= 0:
        return 0
    shifts = get_shift_defs(db)
    total_hours = sum(shift_duration_hours(s) for s in shifts) or 24.0
    hours = shift_duration_hours(shift)
    return max(1, round(daily_target * (hours / total_hours)))
