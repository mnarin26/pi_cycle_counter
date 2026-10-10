"""Working-time calendar for the planner (Europe/Istanbul shifts).

Intervals are sorted, non-overlapping `(start_utc, end_utc)` tuples.
"Hard" rules remove time; "soft" rules only appear in the strict variant, so
the engine can try strict first and fall back to relaxed when a due date
would otherwise be missed.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from app.services.planning.prefs import LineRule, PlanPrefs
from app.services.shifts import ShiftDef
from app.services.time_windows import DISPLAY_TZ

Interval = tuple[datetime, datetime]
_END_OF_DAY = time(23, 59, 59, 999999)


def _is_night(s: ShiftDef) -> bool:
    return s.start.hour < 6 or s.start.hour >= 22


def day_order(shifts: list[ShiftDef]) -> list[ShiftDef]:
    """Shifts in the order a limited daily budget fills them: day shifts first
    (by start time), night shifts last."""
    return sorted(shifts, key=lambda s: (_is_night(s), s.start.hour, s.start.minute))


def _shift_span(day: date, s: ShiftDef) -> tuple[datetime, datetime]:
    start = datetime.combine(day, s.start, tzinfo=DISPLAY_TZ)
    if s.end == _END_OF_DAY:
        end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=DISPLAY_TZ)
    else:
        end = datetime.combine(day, s.end, tzinfo=DISPLAY_TZ)
        if end <= start:
            end += timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _merge(iv: list[Interval]) -> list[Interval]:
    iv = sorted(iv)
    out: list[Interval] = []
    for s, e in iv:
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s = max(a[i][0], b[j][0])
        e = min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


class WorkCalendar:
    """Production/changeover intervals from the production-settings shifts.

    A `LineRule` narrows one order line's calendar: `hours_per_day` keeps that
    many hours per local day (day shifts first, the last one cut short) and its
    weekend values override the plan-wide defaults.
    """

    def __init__(self, prefs: PlanPrefs, shifts: list[ShiftDef], start: datetime, days: int = 150):
        self.prefs = prefs
        self.start = start
        self.shifts = day_order(shifts)
        self.night_ids = {s.id for s in self.shifts if _is_night(s)}
        self._cache: dict[tuple, list[Interval]] = {}
        first = start.astimezone(DISPLAY_TZ).date() - timedelta(days=1)
        self.days = [first + timedelta(days=i) for i in range(days + 2)]
        self.closed = set(prefs.closed_days)

    def _weekend(self, rule: LineRule | None) -> tuple[str, str]:
        p = self.prefs
        return (
            (rule.saturday if rule and rule.saturday else p.saturday),
            (rule.sunday if rule and rule.sunday else p.sunday),
        )

    def has_soft_rules(self, manual: bool, rule: LineRule | None = None) -> bool:
        p = self.prefs
        sat, sun = self._weekend(rule)
        return (
            sat == "avoid"
            or sun == "avoid"
            or (manual and p.manual_not_night == "soft")
            or p.changeover_daytime == "soft"
        )

    def _day_blocked(self, day: date, strict: bool, rule: LineRule | None) -> bool:
        if day.isoformat() in self.closed:
            return True
        wd = day.weekday()
        sat, sun = self._weekend(rule)
        mode = sat if wd == 5 else sun if wd == 6 else "work"
        return mode == "off" or (strict and mode == "avoid")

    def production(self, *, manual: bool, strict: bool, rule: LineRule | None = None) -> list[Interval]:
        key = ("prod", manual, strict, rule.cache_key() if rule else None)
        if key in self._cache:
            return self._cache[key]
        lvl = self.prefs.manual_not_night
        skip_night = manual and (lvl == "hard" or (strict and lvl == "soft"))
        budget_h = rule.hours_per_day if rule else None
        raw: list[Interval] = []
        for day in self.days:
            if self._day_blocked(day, strict, rule):
                continue
            left = budget_h * 3600.0 if budget_h is not None else None
            for s in self.shifts:
                if skip_night and s.id in self.night_ids:
                    continue
                a, b = _shift_span(day, s)
                if left is not None:
                    if left <= 0:
                        break
                    span = (b - a).total_seconds()
                    if span > left:
                        b = a + timedelta(seconds=left)
                    left -= min(span, left)
                raw.append((a, b))
        out = [iv for iv in _merge(raw) if iv[1] > self.start]
        self._cache[key] = out
        return out

    def changeover(self, *, manual: bool, strict: bool, rule: LineRule | None = None) -> list[Interval]:
        key = ("co", manual, strict, rule.cache_key() if rule else None)
        if key in self._cache:
            return self._cache[key]
        base = self.production(manual=manual, strict=strict, rule=rule)
        lvl = self.prefs.changeover_daytime
        if lvl == "hard" or (strict and lvl == "soft"):
            a, b = self.prefs.window_minutes()
            win = []
            for day in self.days:
                d0 = datetime.combine(day, time(0), tzinfo=DISPLAY_TZ)
                win.append(
                    (
                        (d0 + timedelta(minutes=a)).astimezone(timezone.utc),
                        (d0 + timedelta(minutes=b)).astimezone(timezone.utc),
                    )
                )
            base = _intersect(base, _merge(win))
        self._cache[key] = base
        return base

    def relaxed_labels(self, a: datetime, b: datetime, *, manual: bool, rule: LineRule | None = None) -> list[str]:
        """Which soft preferences a relaxed [a, b] work span actually broke."""
        out: list[str] = []
        p = self.prefs
        sat, sun = self._weekend(rule)
        cur = a.astimezone(DISPLAY_TZ).date()
        last = b.astimezone(DISPLAY_TZ).date()
        while cur <= last:
            wd = cur.weekday()
            if wd == 5 and sat == "avoid" and "Cumartesi kullanıldı" not in out:
                out.append("Cumartesi kullanıldı")
            if wd == 6 and sun == "avoid" and "Pazar kullanıldı" not in out:
                out.append("Pazar kullanıldı")
            cur += timedelta(days=1)
        if manual and p.manual_not_night == "soft" and self.night_ids:
            strict = self.production(manual=True, strict=True, rule=rule)
            relaxed = self.production(manual=True, strict=False, rule=rule)
            if worked_between(strict, a, b) < worked_between(relaxed, a, b):
                out.append("manuel kalıp gece vardiyasında çalıştı")
        return out


def fit_contiguous(iv: list[Interval], t: datetime, dur_s: float) -> datetime | None:
    """Earliest s >= t with [s, s+dur] inside one interval."""
    for s, e in iv:
        if e <= t:
            continue
        s2 = max(s, t)
        if (e - s2).total_seconds() >= dur_s:
            return s2
    return None


def consume(iv: list[Interval], t: datetime, dur_s: float) -> tuple[datetime, datetime] | None:
    """Spend `dur_s` working seconds from t (pausing in gaps) → (first, end)."""
    remaining = float(dur_s)
    first: datetime | None = None
    for s, e in iv:
        if e <= t:
            continue
        s2 = max(s, t)
        if first is None:
            first = s2
        avail = (e - s2).total_seconds()
        if avail >= remaining:
            return first, s2 + timedelta(seconds=remaining)
        remaining -= avail
    return None


def worked_between(iv: list[Interval], a: datetime, b: datetime) -> float:
    tot = 0.0
    for s, e in iv:
        s2, e2 = max(s, a), min(e, b)
        if e2 > s2:
            tot += (e2 - s2).total_seconds()
    return tot
