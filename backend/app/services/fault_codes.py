"""Diagnosis fault catalog — stable codes for UI and future events."""

from __future__ import annotations

from typing import Any, TypedDict


class FaultDef(TypedDict):
    code: str
    name: str
    title_tr: str
    description_tr: str


# Add new faults here; keep codes stable (E001, E002, …).
# Resolve condition is decided by the caller per code (not global).
FAULT_CATALOG: dict[str, FaultDef] = {
    "E001": {
        "code": "E001",
        "name": "REFLECTOR_LOST",
        "title_tr": "Reflektör bulunamadı",
        "description_tr": (
            "Takip çizgisinde reflektör tespit edilemedi (occlusion grace dışı). "
            "Alarm reflektör tekrar bulununca kapanır; örnekler 1 dk aralıkla alınır."
        ),
    },
}

# Interval between samples for the same active alarm.
FAULT_SAMPLE_INTERVAL_S = 60.0
# Auto-delete rows older than this.
FAULT_RETENTION_DAYS = 7


def get_fault(code: str) -> FaultDef | None:
    return FAULT_CATALOG.get(code)


def list_faults() -> list[FaultDef]:
    return [FAULT_CATALOG[k] for k in sorted(FAULT_CATALOG.keys())]


def suggest_for_code(code: str, detail: dict[str, Any] | None) -> list[str]:
    """Rule-based suggestions for alarm detail UI. Safe if detail is empty."""
    d = detail or {}
    tips: list[str] = []
    if code == "E001":
        seg = d.get("segment_len")
        lo = d.get("len_min")
        hi = d.get("len_max")
        try:
            seg_i = int(seg) if seg is not None else None
            lo_i = int(lo) if lo is not None else None
            hi_i = int(hi) if hi is not None else None
        except (TypeError, ValueError):
            seg_i = lo_i = hi_i = None
        if seg_i is not None and lo_i is not None and hi_i is not None:
            if seg_i < lo_i or seg_i > hi_i:
                tips.append(
                    f"Aktüel len değeri sınır dışında ({seg_i}, beklenen {lo_i}–{hi_i}). "
                    "Uzunluk min/max sınırını genişletin veya kalibrasyonu yenileyin."
                )
        elif seg_i is not None and (lo_i is None or hi_i is None):
            tips.append(
                f"Len={seg_i} görünüyor ama min/max tanımlı değil. "
                "Admin’de uzunluk kalibrasyonu yapın."
            )

        delta = d.get("delta")
        thr = d.get("threshold_active")
        try:
            delta_i = int(delta) if delta is not None else None
            thr_i = int(thr) if thr is not None else None
        except (TypeError, ValueError):
            delta_i = thr_i = None
        if delta_i is not None and thr_i is not None and delta_i < thr_i:
            tips.append(
                f"Δ değeri eşikten küçük ({delta_i} < {thr_i}). "
                "Tespit eşiğini (threshold_min / offset) düşürün veya ışığı iyileştirin."
            )
        elif delta_i is not None and thr_i is not None and delta_i >= thr_i:
            tips.append(
                f"Δ eşik üzerinde ({delta_i} ≥ {thr_i}) ama reflektör bulunamadı — "
                "len filtresi veya tek-örnek gürültü reddi olabilir; len sınırlarını kontrol edin."
            )

        peak = d.get("peak")
        bg = d.get("background")
        try:
            peak_i = int(peak) if peak is not None else None
            bg_i = int(bg) if bg is not None else None
        except (TypeError, ValueError):
            peak_i = bg_i = None
        if peak_i is not None and bg_i is not None and (peak_i - bg_i) < 20:
            tips.append(
                f"Peak/bg kontrast düşük ({peak_i}/{bg_i}). "
                "Işık, ROI hizası veya çizgi kalınlığını kontrol edin."
            )

        if not tips:
            tips.append(
                "Reflektör görünmüyor. Takip çizgisi hizası, ışık ve eşik ayarlarını kontrol edin."
            )
    else:
        tips.append("Bu hata kodu için özel öneri yok. ROI / kamera / eşik ayarlarını kontrol edin.")
    return tips
