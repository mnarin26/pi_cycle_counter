"""Global vision sampling / timing settings (AppSetting global.vision)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.stored_settings import get_section, patch_section

DEFAULT_SAMPLE_INTERVAL_MS = 100
MIN_SAMPLE_INTERVAL_MS = 50
MAX_SAMPLE_INTERVAL_MS = 500
DEFAULT_MIN_CYCLE_S = 3.0


def vision_public_view(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        ms = int(raw.get("sample_interval_ms", DEFAULT_SAMPLE_INTERVAL_MS))
    except (TypeError, ValueError):
        ms = DEFAULT_SAMPLE_INTERVAL_MS
    ms = max(MIN_SAMPLE_INTERVAL_MS, min(MAX_SAMPLE_INTERVAL_MS, ms))

    try:
        min_cycle = float(raw.get("schmitt_min_cycle_s", DEFAULT_MIN_CYCLE_S))
    except (TypeError, ValueError):
        min_cycle = DEFAULT_MIN_CYCLE_S
    min_cycle = max(3.0, min(120.0, min_cycle))

    return {
        "sample_interval_ms": ms,
        "target_fps": max(1, min(20, int(1000 / ms))),
        "schmitt_min_cycle_s": min_cycle,
    }


def get_vision_settings(db: Session | None = None) -> dict[str, Any]:
    if db is None:
        return vision_public_view({})
    return vision_public_view(get_section(db, "vision"))


def patch_vision_settings(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_section(db, "vision")
    updated = dict(current)
    if "sample_interval_ms" in patch and patch["sample_interval_ms"] is not None:
        ms = int(patch["sample_interval_ms"])
        if ms < MIN_SAMPLE_INTERVAL_MS or ms > MAX_SAMPLE_INTERVAL_MS:
            raise ValueError(
                f"Islem araligi {MIN_SAMPLE_INTERVAL_MS}–{MAX_SAMPLE_INTERVAL_MS} ms olmali"
            )
        updated["sample_interval_ms"] = ms
    if "schmitt_min_cycle_s" in patch and patch["schmitt_min_cycle_s"] is not None:
        mc = float(patch["schmitt_min_cycle_s"])
        if mc < 3.0 or mc > 120.0:
            raise ValueError("Schmitt min dongu 3–120 sn arasinda olmali")
        updated["schmitt_min_cycle_s"] = mc
    patch_section(db, "vision", updated)
    return get_vision_settings(db)
