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
    return ShiftDef(id=sid, name=name, start=start, end=end)


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


def shift_window(shift: ShiftDef, at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC bounds for the shift instance containing `at` (Istanbul wall clock)."""
    now_utc = ensure_utc(at or datetime.now(timezone.utc))
    local = now_utc.astimezone(DISPLAY_TZ)
    day = local.date()
    start_local = _local_dt(day, shift.start)
    end_local = _local_dt(day, shift.end)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    if local < start_local:
        start_local -= timedelta(days=1)
        end_local -= timedelta(days=1)
    elif local >= end_local:
        start_local += timedelta(days=1)
        end_local += timedelta(days=1)
    return start_local.astimezone(timezone.utc), min(end_local.astimezone(timezone.utc), now_utc)


def current_shift(db: Session | None = None, at: datetime | None = None) -> ShiftDef:
    now_utc = ensure_utc(at or datetime.now(timezone.utc))
    local = now_utc.astimezone(DISPLAY_TZ)
    t = local.time().replace(microsecond=0)
    for shift in get_shift_defs(db):
        start = shift.start
        end = shift.end
        if end > start:
            if start <= t < end:
                return shift
        else:
            if t >= start or t < end:
                return shift
    return get_shift_defs(db)[0]


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
