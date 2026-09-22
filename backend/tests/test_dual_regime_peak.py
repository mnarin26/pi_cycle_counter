"""Unit tests for keyed two-regime reflector detection."""

from __future__ import annotations

import numpy as np

from app.vision.line_pipeline import (
    LOCAL_NEIGHBOR_DROP,
    REGIME_ENTER,
    REGIME_EXIT,
    find_peak_on_profile,
    line_peak_on_smooth_profile,
    resolve_detect_regime,
)


def _legacy_effective_found(found: bool, segment_len: int, prominence: int, *, dual_regime: bool) -> bool:
    """Mirrors orchestrator branch: 42 veto only when flag OFF."""
    if dual_regime:
        return bool(found)
    if found and segment_len == 1 and prominence < 42:
        return False
    return bool(found)


def test_hysteresis_enter_exit_hold():
    assert resolve_detect_regime(None, REGIME_ENTER) == "local"
    assert resolve_detect_regime("global", REGIME_ENTER) == "local"
    assert resolve_detect_regime("local", 0) == "local"

    assert resolve_detect_regime(None, REGIME_EXIT) == "global"
    assert resolve_detect_regime("local", REGIME_EXIT) == "global"
    assert resolve_detect_regime("global", 200) == "global"

    # Hold band 76–99
    assert resolve_detect_regime("local", 93) == "local"
    assert resolve_detect_regime("global", 93) == "global"
    assert resolve_detect_regime(None, 93) == "global"
    assert resolve_detect_regime("local", 76) == "local"
    assert resolve_detect_regime("global", 99) == "global"


def test_flag_off_legacy_len1_delta41_veto():
    """E001-style: Δ=41 len=1 — pipeline may find; orchestrator 42 veto when flag OFF."""
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[20] = 141.0  # Δ=41 vs median 100
    xs = np.arange(40, dtype=np.float32)
    ys = np.zeros(40, dtype=np.float32)

    legacy = find_peak_on_profile(smooth, xs, ys, prominence_min=20)
    assert legacy.prominence == 41
    assert legacy.segment_len == 1
    assert legacy.found is True  # pipeline accepts; veto is orchestrator-side

    assert _legacy_effective_found(legacy.found, legacy.segment_len, legacy.prominence, dual_regime=False) is False

    # Flag OFF path via helper must not attach dual telemetry / local path
    off = line_peak_on_smooth_profile(
        smooth, xs, ys, prominence_min=20, dual_regime=False
    )
    assert off.detect_regime is None
    assert off.found is True
    assert _legacy_effective_found(off.found, off.segment_len, off.prominence, dual_regime=False) is False


def test_flag_on_len1_delta41_local_can_find():
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[20] = 141.0  # Δ=41 → local regime; neighbor drop 41 ≥ 20
    xs = np.arange(40, dtype=np.float32)
    ys = np.zeros(40, dtype=np.float32)

    on = line_peak_on_smooth_profile(
        smooth,
        xs,
        ys,
        prominence_min=20,
        dual_regime=True,
        prev_regime="global",
    )
    assert on.detect_regime == "local"
    assert on.global_delta == 41
    assert on.local_delta >= LOCAL_NEIGHBOR_DROP
    assert on.found is True
    assert on.segment_len == 1  # len==1 alone is not a reject
    assert _legacy_effective_found(on.found, on.segment_len, on.prominence, dual_regime=True) is True


def test_flag_on_len_max_overflow_falls_to_local_cluster():
    """Δ=93 expand len=8 with max 5 → local finds ~2 cluster in 1–5."""
    smooth = np.full(40, 158.0, dtype=np.float32)
    smooth[10:18] = 210.0  # 8-sample sled above midpoint
    smooth[13] = 245.0
    smooth[14] = 251.0
    xs = np.arange(40, dtype=np.float32)
    ys = np.zeros(40, dtype=np.float32)

    # Sanity: global hard path rejects on len_max
    hard = find_peak_on_profile(smooth, xs, ys, prominence_min=30, reflector_len_min=1, reflector_len_max=5)
    assert hard.segment_len == 8
    assert hard.found is False

    # Dual ON, hold/global band: soft overflow → local short cluster
    on = line_peak_on_smooth_profile(
        smooth,
        xs,
        ys,
        prominence_min=30,
        reflector_len_min=1,
        reflector_len_max=5,
        dual_regime=True,
        prev_regime="global",  # Δ=93 holds global, then len>max forces local method
    )
    assert on.global_delta == 93
    assert on.detect_regime == "local"
    assert on.found is True
    assert 1 <= on.segment_len <= 5
    assert on.segment_len <= 3  # short cluster, not the 8-wide sled


def test_flag_on_flat_profile_reject():
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[20] = 110.0  # spread 10 < 16
    on = line_peak_on_smooth_profile(smooth, prominence_min=10, dual_regime=True, prev_regime="local")
    assert on.found is False


def test_flag_on_local_contrast_below_prom_min_reject():
    """Soft plateau in local regime: local contrast below machine prom_min → reject."""
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[15:25] = 130.0
    smooth[20] = 141.0  # Δ=41 → local; cluster expands on soft sled; local_delta < 50
    on = line_peak_on_smooth_profile(
        smooth, prominence_min=50, dual_regime=True, prev_regime="local"
    )
    assert on.found is False
    assert on.detect_regime == "local"


def test_flag_off_does_not_use_soft_len_max():
    smooth = np.full(40, 158.0, dtype=np.float32)
    smooth[10:18] = 210.0
    smooth[13] = 245.0
    smooth[14] = 251.0
    off = line_peak_on_smooth_profile(
        smooth,
        prominence_min=30,
        reflector_len_min=1,
        reflector_len_max=5,
        dual_regime=False,
    )
    assert off.found is False
    assert off.detect_regime is None
    assert off.segment_len == 8
    assert off.reject_reason is None


def test_flag_on_local_cluster_outside_len_max_sets_reject_reason():
    """Wide bright island: local expand stays > len_max → reject_reason=len_band (E003)."""
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[17:23] = 240.0  # 6-sample plateau; neighbor drop keeps all 6
    xs = np.arange(40, dtype=np.float32)
    ys = np.zeros(40, dtype=np.float32)

    on = line_peak_on_smooth_profile(
        smooth,
        xs,
        ys,
        prominence_min=20,
        reflector_len_min=1,
        reflector_len_max=3,
        dual_regime=True,
        prev_regime="local",
    )
    assert on.found is False
    assert on.detect_regime == "local"
    assert on.segment_len > 3
    assert on.reject_reason == "len_band"


def test_flag_on_successful_find_clears_reject_reason():
    smooth = np.full(40, 100.0, dtype=np.float32)
    smooth[20] = 141.0
    on = line_peak_on_smooth_profile(
        smooth, prominence_min=20, dual_regime=True, prev_regime="local"
    )
    assert on.found is True
    assert on.reject_reason is None
