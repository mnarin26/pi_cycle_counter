"""Diagnosis Telegram alerts: recipient window + message formatting.

No SQLite and no HTTP. Vision threads must not import callers that talk
to Telegram; this module is used by the bot process and unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.fault_codes import suggest_for_code
from app.services.stored_settings import (
    ALERT_PERMISSION_KEY,
    coerce_alert_prefs,
    normalize_operators,
)

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")


def _minutes_from_midnight(hhmm: str) -> int | None:
    s = (hhmm or "").strip().replace(".", ":")
    parts = s.split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
    except (TypeError, ValueError):
        return None
    if h == 24 and m == 0:
        return 24 * 60
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def in_alert_window(
    prefs: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True if `now` (UTC or aware) falls in the operator's days + hours."""
    p = coerce_alert_prefs(prefs)
    days = p.get("weekdays") or []
    if not days:
        return False
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(DISPLAY_TZ)
    if int(local.weekday()) not in {int(d) for d in days}:
        return False
    start = _minutes_from_midnight(str(p.get("time_start") or "08:00"))
    end = _minutes_from_midnight(str(p.get("time_end") or "18:00"))
    if start is None or end is None:
        return False
    cur = local.hour * 60 + local.minute
    if start == end:
        return True
    if start < end:
        return start <= cur <= end
    return cur >= start or cur <= end


def operator_receives_alerts(op: dict[str, Any], *, now: datetime | None = None) -> bool:
    perms = op.get("permissions") or {}
    if not bool(perms.get(ALERT_PERMISSION_KEY, False)):
        return False
    tg = str(op.get("telegram_user_id") or "").strip()
    if not tg.isdigit():
        return False
    return in_alert_window(op.get("alert_prefs"), now=now)


def recipients_for_alert(
    telegram_cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Operators who should receive a Telegram diagnosis alert right now."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for op in normalize_operators(telegram_cfg):
        if not operator_receives_alerts(op, now=now):
            continue
        tg = str(op.get("telegram_user_id") or "").strip()
        if tg in seen:
            continue
        seen.add(tg)
        prefs = coerce_alert_prefs(op.get("alert_prefs"))
        out.append(
            {
                "id": op.get("id"),
                "name": op.get("name") or "",
                "telegram_user_id": tg,
                "include_details": bool(prefs.get("include_details")),
            }
        )
    return out


def format_headline(machine_name: str | None, title_tr: str | None, *, machine_id: int | None = None) -> str:
    machine = (machine_name or "").strip() or (f"Makine {machine_id}" if machine_id else "Makine")
    title = (title_tr or "").strip() or "Alarm"
    return f"{machine} {title}"


def format_detail_message(code: str, detail: dict[str, Any] | None) -> str | None:
    d = detail if isinstance(detail, dict) else {}
    lines: list[str] = []
    peak = d.get("peak")
    bg = d.get("background")
    if peak is not None or bg is not None:
        lines.append(f"Peak / bg: {peak if peak is not None else '—'} / {bg if bg is not None else '—'}")
    delta = d.get("delta")
    thr = d.get("threshold_active")
    off = d.get("threshold_offset")
    mode = d.get("threshold_mode")
    if thr is not None or off is not None or mode:
        bits = []
        if thr is not None:
            bits.append(str(thr))
        if off is not None:
            bits.append(f"ofset {off}")
        if mode:
            bits.append(str(mode))
        lines.append("Eşik: " + ", ".join(bits))
    if delta is not None:
        lines.append(f"Δ: {delta}")
    seg = d.get("segment_len")
    lo = d.get("len_min")
    hi = d.get("len_max")
    if seg is not None or lo is not None or hi is not None:
        span = ""
        if lo is not None or hi is not None:
            span = f" (min {lo if lo is not None else '—'} – max {hi if hi is not None else '—'})"
        lines.append(f"Len: {seg if seg is not None else '—'}{span}")
    thick = d.get("line_thickness")
    if thick is not None:
        lines.append(f"Kalınlık: {thick}")
    used = {
        "peak",
        "background",
        "delta",
        "threshold_active",
        "threshold_offset",
        "threshold_mode",
        "segment_len",
        "len_min",
        "len_max",
        "line_thickness",
    }
    extra = [k for k in d.keys() if k not in used and d[k] is not None]
    for k in extra:
        lines.append(f"{k}: {d[k]}")
    tips = suggest_for_code(code, d)
    if tips:
        if lines:
            lines.append("")
        lines.append("Öneri:")
        lines.extend(f"- {t}" for t in tips)
    text = "\n".join(lines).strip()
    return text or None
