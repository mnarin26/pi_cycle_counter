"""TV wall and shift configuration stored in AppSetting global.production."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.stored_settings import get_section, patch_section

DEFAULT_SHIFTS: list[dict[str, str]] = [
    {"id": "night", "name": "Gece", "start": "00:00", "end": "08:00"},
    {"id": "day", "name": "Gündüz", "start": "08:00", "end": "16:00"},
    {"id": "evening", "name": "Akşam", "start": "16:00", "end": "24:00"},
]

DEFAULT_TV_ROTATE_SECONDS = 20
MIN_TV_ROTATE_SECONDS = 5
MAX_TV_ROTATE_SECONDS = 300
MAX_SHIFTS = 4
MIN_SHIFTS = 1


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Saat formatı HH:MM olmalı: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour == 24 and minute == 0:
        return 24, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Geçersiz saat: {value}")
    return hour, minute


def _normalize_shift(raw: dict[str, Any], index: int) -> dict[str, str]:
    sid = str(raw.get("id") or f"shift_{index + 1}").strip()
    name = str(raw.get("name") or f"Vardiya {index + 1}").strip()
    start = str(raw.get("start") or "").strip()
    end = str(raw.get("end") or "").strip()
    if not name:
        raise ValueError(f"Vardiya {index + 1}: ad boş olamaz")
    _parse_hhmm(start)
    _parse_hhmm(end)
    return {"id": sid, "name": name, "start": start, "end": end}


def validate_shifts(shifts: list[dict[str, Any]]) -> list[dict[str, str]]:
    if len(shifts) < MIN_SHIFTS:
        raise ValueError(f"En az {MIN_SHIFTS} vardiya tanımlanmalı")
    if len(shifts) > MAX_SHIFTS:
        raise ValueError(f"En fazla {MAX_SHIFTS} vardiya tanımlanabilir")
    out: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(shifts):
        if not isinstance(item, dict):
            raise ValueError(f"Vardiya {i + 1}: geçersiz kayıt")
        row = _normalize_shift(item, i)
        if row["id"] in seen_ids:
            raise ValueError(f"Vardiya kimliği tekrarlı: {row['id']}")
        seen_ids.add(row["id"])
        out.append(row)
    return out


def production_public_view(raw: dict[str, Any]) -> dict[str, Any]:
    rotate = raw.get("tv_rotate_seconds", DEFAULT_TV_ROTATE_SECONDS)
    try:
        rotate_i = int(rotate)
    except (TypeError, ValueError):
        rotate_i = DEFAULT_TV_ROTATE_SECONDS
    rotate_i = max(MIN_TV_ROTATE_SECONDS, min(MAX_TV_ROTATE_SECONDS, rotate_i))

    shifts_raw = raw.get("shifts")
    if isinstance(shifts_raw, list) and shifts_raw:
        try:
            shifts = validate_shifts(shifts_raw)
        except ValueError:
            shifts = list(DEFAULT_SHIFTS)
    else:
        shifts = list(DEFAULT_SHIFTS)

    return {
        "tv_rotate_seconds": rotate_i,
        "shift_count": len(shifts),
        "shifts": shifts,
    }


def get_production_settings(db: Session) -> dict[str, Any]:
    return production_public_view(get_section(db, "production"))


def patch_production_settings(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_section(db, "production")
    updated = dict(current)

    if "tv_rotate_seconds" in patch and patch["tv_rotate_seconds"] is not None:
        rotate = int(patch["tv_rotate_seconds"])
        if rotate < MIN_TV_ROTATE_SECONDS or rotate > MAX_TV_ROTATE_SECONDS:
            raise ValueError(
                f"TV geçiş süresi {MIN_TV_ROTATE_SECONDS}–{MAX_TV_ROTATE_SECONDS} saniye arasında olmalı"
            )
        updated["tv_rotate_seconds"] = rotate

    if "shifts" in patch and patch["shifts"] is not None:
        updated["shifts"] = validate_shifts(patch["shifts"])

    patch_section(db, "production", updated)
    return get_production_settings(db)


def get_tv_rotate_seconds(db: Session | None = None) -> int:
    if db is None:
        return DEFAULT_TV_ROTATE_SECONDS
    return int(get_production_settings(db)["tv_rotate_seconds"])
