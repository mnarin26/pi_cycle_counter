"""Calendar period windows: today / this week / month / year start → now (Europe/Istanbul)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")
RangeKey = Literal["daily", "weekly", "monthly", "yearly"]


def period_start_utc(range_: str, now: datetime | None = None) -> datetime:
    """Start of current calendar period in Istanbul, as UTC for DB filters."""
    now_utc = now or datetime.now(timezone.utc)
    local = now_utc.astimezone(DISPLAY_TZ)
    d = local.date()

    if range_ == "daily":
        start_local = datetime(d.year, d.month, d.day, tzinfo=DISPLAY_TZ)
    elif range_ == "weekly":
        monday = d - timedelta(days=d.weekday())
        start_local = datetime(monday.year, monday.month, monday.day, tzinfo=DISPLAY_TZ)
    elif range_ == "monthly":
        start_local = datetime(d.year, d.month, 1, tzinfo=DISPLAY_TZ)
    elif range_ == "yearly":
        start_local = datetime(d.year, 1, 1, tzinfo=DISPLAY_TZ)
    else:
        start_local = datetime(d.year, d.month, d.day, tzinfo=DISPLAY_TZ)

    return start_local.astimezone(timezone.utc)


def resolve_window(
    range_: RangeKey,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = from_ts if from_ts else period_start_utc(range_, now)
    end = to_ts or now
    if end <= start:
        end = start + timedelta(seconds=1)
    return start, end


def format_window_label(range_: str, start: datetime, end: datetime) -> str:
    s = start.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M")
    e = end.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M")
    titles = {
        "daily": "Bugün",
        "weekly": "Bu hafta (Pzt → şimdi)",
        "monthly": "Bu ay",
        "yearly": "Bu yıl",
    }
    title = titles.get(range_, range_)
    return f"{title}: {s} → {e}"
