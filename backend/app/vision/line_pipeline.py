"""1D line-probe reflector tracker.

Design rationale (decided after blob/centroid attempts failed):

We sample brightness along a single user-defined line (closed_end -> open_end)
with an optional perpendicular thickness (a few pixels on each side averaged
via max-pooling, which is robust to motion blur and small jitter). The
reflector's location along the line is the position of the brightness peak in
that 1D profile.

This is dramatically simpler and more stable than 2D blob detection because:

- The search space is one-dimensional. False bright spots outside the line
  are physically invisible to the algorithm.
- Motion blur smears the reflector but the peak position barely shifts.
- Cost is O(N) on a tiny array (N ~ line length, usually < 300), so each
  machine costs well under a millisecond even on a Pi 3B.

Detection is done in two stages:

1. Sample the profile via bilinear interpolation along the line, taking the
   max over a perpendicular window (`thickness_px`). Max-pool is preferred
   over mean because a saturated reflector overlapping any row of the window
   should still dominate.
2. Find the peak. Acceptance is based on `prominence = peak - background`,
   where background = median of the profile. This is invariant to ambient
   light: only the *relative* peak matters. The user controls a single
   "prominence_min" threshold; an optional offset is added on top.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np


@dataclass
class LineProbeResult:
    found: bool
    position_01: float | None  # 0..1 along axis_p0 -> axis_p1
    centroid_px: tuple[float, float] | None  # global pixel coords for UI overlay
    peak: int  # raw 0..255
    background: int  # raw 0..255 (median of profile)
    prominence: int  # peak - background
    segment_len: int  # contiguous bright segment length around chosen peak (in samples)
    active_threshold: int  # threshold used this frame (for UI)
    # Dual-regime telemetry (populated only when reflector_dual_regime path runs).
    detect_regime: str | None = None  # "global" | "local"
    global_delta: int | None = None
    local_delta: int | None = None
    reliability_score: float | None = None


# Dual-regime constants (code-only; not UI knobs).
REGIME_ENTER = 75  # global_Δ ≤ 75 → enter local
REGIME_EXIT = 100  # global_Δ ≥ 100 → exit to global
LOCAL_NEIGHBOR_DROP = 20  # min peak-to-neighbor contrast for local cluster


@lru_cache(maxsize=256)
def _parse_xy(s: str) -> tuple[float, float]:
    """Parse a normalized "[x, y]" JSON string. Cached: the same axis strings are
    re-sent every frame for every machine, so parsing them once is enough."""
    arr = json.loads(s)
    return float(arr[0]), float(arr[1])


# ---------------------------------------------------------------------------
# Probe geometry cache
#
# For a fixed (p0, p1, thickness, num_samples, frame size) the sampling grid
# (linspace / perpendicular offsets / clip / float32 cast) is identical every
# frame. We compute it once and keep the resulting `map_x`, `map_y` (for
# cv2.remap) and `cx`, `cy` (sample centers, returned to the caller) in a small
# LRU dict so the per-frame hot path is just remap + max-pool.
#
# Cached arrays are marked non-writeable and must never be mutated by callers.
# ---------------------------------------------------------------------------
_PROBE_GEOM_CACHE_MAX = 64
_probe_geom_cache: dict = {}
_probe_geom_lock = threading.Lock()


def _probe_geometry_key(
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int,
    num_samples: int | None,
    w: int,
    h: int,
) -> tuple:
    # Round to 1e-4 px: sub-pixel jitter below that is irrelevant for sampling
    # but would otherwise defeat the cache when coordinates come from float math.
    return (
        round(float(p0_xy[0]), 4),
        round(float(p0_xy[1]), 4),
        round(float(p1_xy[0]), 4),
        round(float(p1_xy[1]), 4),
        int(thickness_px),
        int(num_samples) if num_samples else 0,
        int(w),
        int(h),
    )


def _build_probe_geometry(
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int,
    num_samples: int | None,
    w: int,
    h: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute (map_x, map_y, cx, cy) for a line probe. All float32, read-only."""
    ax, ay = p0_xy
    bx, by = p1_xy
    dx, dy = bx - ax, by - ay
    length = float(np.hypot(dx, dy))

    n = int(num_samples) if num_samples else max(8, int(round(length)))
    # unit direction
    ux, uy = dx / length, dy / length
    # perpendicular
    vx, vy = -uy, ux

    ts = np.linspace(0.0, 1.0, n, dtype=np.float32)
    cx = ax + ts * dx
    cy = ay + ts * dy

    half = max(0, int(thickness_px) // 2)
    # Sample along perpendicular: indices -half..+half (inclusive)
    offsets = np.arange(-half, half + 1, dtype=np.float32) if half > 0 else np.array([0.0], dtype=np.float32)

    # Build sample grid: shape (len(offsets), n)
    xs = cx[None, :] + offsets[:, None] * vx
    ys = cy[None, :] + offsets[:, None] * vy

    # Clamp to frame
    xs_c = np.clip(xs, 0, w - 1)
    ys_c = np.clip(ys, 0, h - 1)

    # cv2.remap requires float32 maps and is the fastest bilinear sampler.
    map_x = xs_c.astype(np.float32)
    map_y = ys_c.astype(np.float32)
    cx32 = cx.astype(np.float32)
    cy32 = cy.astype(np.float32)
    for arr in (map_x, map_y, cx32, cy32):
        arr.flags.writeable = False
    return map_x, map_y, cx32, cy32


def _get_probe_geometry(
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int,
    num_samples: int | None,
    w: int,
    h: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    key = _probe_geometry_key(p0_xy, p1_xy, thickness_px, num_samples, w, h)
    with _probe_geom_lock:
        geom = _probe_geom_cache.pop(key, None)
        if geom is not None:
            # Re-insert to mark as most recently used (dict keeps insertion order).
            _probe_geom_cache[key] = geom
            return geom
    geom = _build_probe_geometry(p0_xy, p1_xy, thickness_px, num_samples, w, h)
    with _probe_geom_lock:
        if key not in _probe_geom_cache:
            while len(_probe_geom_cache) >= _PROBE_GEOM_CACHE_MAX:
                _probe_geom_cache.pop(next(iter(_probe_geom_cache)))
            _probe_geom_cache[key] = geom
    return geom


def sample_line_profile_gray(
    gray: np.ndarray,
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int = 7,
    num_samples: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a 1D brightness profile from a precomputed grayscale frame.

    For each of N points along the line, we max-pool the grayscale values in
    a perpendicular window of `thickness_px` total pixels (clamped to frame).

    Returns:
        profile: shape (N,) uint8
        sample_xs: shape (N,) float32  -- x pixel coord of each sample center
        sample_ys: shape (N,) float32  -- y pixel coord of each sample center
    """
    h, w = gray.shape[:2]

    ax, ay = p0_xy
    bx, by = p1_xy
    dx, dy = bx - ax, by - ay
    length = float(np.hypot(dx, dy))
    if length < 2.0:
        return np.zeros(2, dtype=np.uint8), np.array([ax, bx], dtype=np.float32), np.array([ay, by], dtype=np.float32)

    # Geometry (sampling grid + sample centers) is identical every frame for a
    # given line/thickness/frame size -> served from a small LRU cache.
    map_x, map_y, cx, cy = _get_probe_geometry(p0_xy, p1_xy, thickness_px, num_samples, w, h)

    sampled = cv2.remap(gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # sampled shape == (len(offsets), n); collapse perpendicular axis via max-pool.
    profile = sampled.max(axis=0).astype(np.uint8)
    # cx / cy are shared read-only cache entries (writeable=False); callers only read them.
    return profile, cx, cy


def sample_line_band_gray(
    gray: np.ndarray,
    axis_p0_json: str,
    axis_p1_json: str,
    thickness_px: int = 7,
    num_samples: int | None = None,
) -> np.ndarray | None:
    """Raw perpendicular×along brightness band (uint8). Uses the same geometry cache.

    Shape: (n_offsets, n_along). Intended for rare diagnosis snapshots only —
    do not call on every vision frame.
    """
    h, w = gray.shape[:2]
    p0n = _parse_xy(axis_p0_json)
    p1n = _parse_xy(axis_p1_json)
    p0 = (p0n[0] * w, p0n[1] * h)
    p1 = (p1n[0] * w, p1n[1] * h)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if float(np.hypot(dx, dy)) < 2.0:
        return None
    map_x, map_y, _cx, _cy = _get_probe_geometry(
        p0, p1, max(1, int(thickness_px)), num_samples, w, h
    )
    sampled = cv2.remap(
        gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    return sampled.astype(np.uint8)


def pack_brightness_band(band: np.ndarray | None, *, preview_scale: int = 8) -> dict:
    """JSON-friendly band: rows = along line, cols = thickness (user NxT view).

    Also packs a nearest-neighbor upscaled JPEG of the same pixels (no overlays /
    no yellow line) for diagnosis UI — only call on rare sample-write path.
    """
    if band is None or getattr(band, "size", 0) == 0:
        return {}
    # remap layout is (thickness_offsets, along); transpose for display
    along_major = np.ascontiguousarray(band.T)
    out: dict = {
        "brightness_band": along_major.tolist(),
        "band_along": int(along_major.shape[0]),
        "band_thickness": int(along_major.shape[1]),
    }
    try:
        scale = max(1, int(preview_scale or 1))
        vis = along_major
        if scale > 1:
            vis = cv2.resize(
                along_major,
                (along_major.shape[1] * scale, along_major.shape[0] * scale),
                interpolation=cv2.INTER_NEAREST,
            )
        ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if ok:
            out["brightness_preview_jpg_b64"] = base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        # Preview is optional; numbers still useful.
        pass
    return out


def sample_line_profile(
    frame_bgr: np.ndarray,
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int = 7,
    num_samples: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a 1D brightness profile along the line p0 -> p1 (BGR input)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return sample_line_profile_gray(gray, p0_xy, p1_xy, thickness_px, num_samples)


def _parabolic_subpixel(profile: np.ndarray, idx: int) -> float:
    """Refine an integer peak index to subpixel precision via parabolic fit."""
    n = len(profile)
    if idx <= 0 or idx >= n - 1:
        return float(idx)
    y0 = float(profile[idx - 1])
    y1 = float(profile[idx])
    y2 = float(profile[idx + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-6:
        return float(idx)
    return float(idx) + 0.5 * (y0 - y2) / denom


def _smooth(profile: np.ndarray, k: int = 3) -> np.ndarray:
    if k and k > 1:
        kernel = np.ones(int(k), dtype=np.float32) / float(k)
        return np.convolve(profile.astype(np.float32), kernel, mode="same")
    return profile.astype(np.float32)


def find_peak_on_profile(
    smooth_profile: np.ndarray,
    sample_xs: np.ndarray,
    sample_ys: np.ndarray,
    prominence_min: int,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
) -> LineProbeResult:
    """Find the brightness peak in a (pre-smoothed) 1D profile with prominence
    gating. Caller computed smoothing + threshold from this same array, so
    decisions are consistent.
    """
    result, _overflow = _find_peak_on_profile_core(
        smooth_profile,
        sample_xs,
        sample_ys,
        prominence_min,
        reflector_len_min=reflector_len_min,
        reflector_len_max=reflector_len_max,
        soft_len_max=False,
    )
    return result


def _find_peak_on_profile_core(
    smooth_profile: np.ndarray,
    sample_xs: np.ndarray,
    sample_ys: np.ndarray,
    prominence_min: int,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
    *,
    soft_len_max: bool = False,
) -> tuple[LineProbeResult, bool]:
    """Core global argmax + midpoint expand.

    When soft_len_max is True and segment_len > len_max, returns
    (not-found result, True) so the dual-regime path can fall through to local
    cluster instead of hard-rejecting.
    """
    n = smooth_profile.size
    if n < 4:
        return LineProbeResult(False, None, None, 0, 0, 0, 0, prominence_min), False

    idx = int(np.argmax(smooth_profile))
    peak = int(round(float(smooth_profile[idx])))
    background = int(round(float(np.median(smooth_profile))))
    prominence = max(0, peak - background)

    # Estimate reflector footprint length around peak.
    # Threshold is midpoint between local background and local peak.
    seg_thr = background + 0.5 * float(prominence)
    left = idx
    while left > 0 and float(smooth_profile[left - 1]) >= seg_thr:
        left -= 1
    right = idx
    while right < n - 1 and float(smooth_profile[right + 1]) >= seg_thr:
        right += 1
    segment_len = int(max(1, right - left + 1))

    if prominence < int(prominence_min):
        return (
            LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min)),
            False,
        )

    if reflector_len_min is not None and segment_len < int(reflector_len_min):
        return (
            LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min)),
            False,
        )
    if reflector_len_max is not None and segment_len > int(reflector_len_max):
        if soft_len_max:
            return (
                LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min)),
                True,
            )
        return (
            LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min)),
            False,
        )

    refined = _parabolic_subpixel(smooth_profile.astype(np.uint8), idx)
    refined = float(np.clip(refined, 0.0, n - 1))
    position_01 = refined / float(n - 1)

    lo = int(np.floor(refined))
    hi = min(n - 1, lo + 1)
    frac = refined - lo
    cx = float(sample_xs[lo]) * (1.0 - frac) + float(sample_xs[hi]) * frac
    cy = float(sample_ys[lo]) * (1.0 - frac) + float(sample_ys[hi]) * frac

    return (
        LineProbeResult(True, position_01, (cx, cy), peak, background, prominence, segment_len, int(prominence_min)),
        False,
    )


def resolve_detect_regime(prev_regime: str | None, global_delta: int) -> str:
    """Hysteresis: Δ≤75 → local, Δ≥100 → global, 76–99 hold previous (default global)."""
    d = int(global_delta)
    if d <= REGIME_ENTER:
        return "local"
    if d >= REGIME_EXIT:
        return "global"
    if prev_regime in ("local", "global"):
        return prev_regime
    return "global"


def _flank_drops(smooth_profile: np.ndarray, left: int, right: int, peak: float) -> list[float]:
    n = int(smooth_profile.size)
    drops: list[float] = []
    if left > 0:
        drops.append(float(peak) - float(smooth_profile[left - 1]))
    if right < n - 1:
        drops.append(float(peak) - float(smooth_profile[right + 1]))
    return drops


def reliability_score_for_segment(
    smooth_profile: np.ndarray,
    left: int,
    right: int,
    peak: float,
    segment_len: int,
    reflector_len_min: int | None,
    reflector_len_max: int | None,
) -> float:
    """Soft reliability: dark flanks + len in range boost (never a hard veto)."""
    score = 0.0
    if reflector_len_min is not None and reflector_len_max is not None:
        if int(reflector_len_min) <= int(segment_len) <= int(reflector_len_max):
            score += 1.0
    elif reflector_len_min is not None and int(segment_len) >= int(reflector_len_min):
        score += 0.5
    elif reflector_len_max is not None and int(segment_len) <= int(reflector_len_max):
        score += 0.5
    drops = _flank_drops(smooth_profile, left, right, peak)
    if drops and min(drops) >= float(LOCAL_NEIGHBOR_DROP):
        score += 1.0
    elif drops and max(drops) >= float(LOCAL_NEIGHBOR_DROP):
        score += 0.5
    return float(score)


def _result_with_centroid(
    *,
    found: bool,
    smooth_profile: np.ndarray,
    sample_xs: np.ndarray,
    sample_ys: np.ndarray,
    idx: int,
    peak: int,
    background: int,
    prominence: int,
    segment_len: int,
    prominence_min: int,
    detect_regime: str | None,
    global_delta: int | None,
    local_delta: int | None,
    reliability_score: float | None,
) -> LineProbeResult:
    if not found:
        return LineProbeResult(
            False,
            None,
            None,
            peak,
            background,
            prominence,
            segment_len,
            int(prominence_min),
            detect_regime=detect_regime,
            global_delta=global_delta,
            local_delta=local_delta,
            reliability_score=reliability_score,
        )
    n = int(smooth_profile.size)
    refined = _parabolic_subpixel(smooth_profile.astype(np.uint8), idx)
    refined = float(np.clip(refined, 0.0, n - 1))
    position_01 = refined / float(n - 1)
    lo = int(np.floor(refined))
    hi = min(n - 1, lo + 1)
    frac = refined - lo
    cx = float(sample_xs[lo]) * (1.0 - frac) + float(sample_xs[hi]) * frac
    cy = float(sample_ys[lo]) * (1.0 - frac) + float(sample_ys[hi]) * frac
    return LineProbeResult(
        True,
        position_01,
        (cx, cy),
        peak,
        background,
        prominence,
        segment_len,
        int(prominence_min),
        detect_regime=detect_regime,
        global_delta=global_delta,
        local_delta=local_delta,
        reliability_score=reliability_score,
    )


def find_local_bright_cluster(
    smooth_profile: np.ndarray,
    sample_xs: np.ndarray,
    sample_ys: np.ndarray,
    prominence_min: int,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
    *,
    global_delta: int | None = None,
) -> LineProbeResult:
    """Brightest short cluster for dual-regime local path.

    No long global midpoint expand. Expand only while samples stay within
    LOCAL_NEIGHBOR_DROP of the peak. Require neighbor contrast ≥ ~20, reject
    flat profiles and local contrast below prom_min. len==1 alone is not a
    reject; when len_min/max are set the cluster length must fit that range.
    """
    n = smooth_profile.size
    if n < 4:
        return LineProbeResult(
            False, None, None, 0, 0, 0, 0, prominence_min, detect_regime="local", global_delta=global_delta or 0, local_delta=0
        )

    line_med = float(np.median(smooth_profile))
    line_peak = float(np.max(smooth_profile))
    g_delta = int(global_delta) if global_delta is not None else int(round(max(0.0, line_peak - line_med)))

    if line_peak - line_med < _MIN_MAX_MEDIAN_SPREAD:
        pk_i = int(round(line_peak))
        bg_i = int(round(line_med))
        prom_i = max(0, pk_i - bg_i)
        return LineProbeResult(
            False,
            None,
            None,
            pk_i,
            bg_i,
            prom_i,
            0,
            int(prominence_min),
            detect_regime="local",
            global_delta=g_delta,
            local_delta=0,
            reliability_score=0.0,
        )

    idx = int(np.argmax(smooth_profile))
    peak_f = float(smooth_profile[idx])
    floor = peak_f - float(LOCAL_NEIGHBOR_DROP)
    left = idx
    while left > 0 and float(smooth_profile[left - 1]) >= floor:
        left -= 1
    right = idx
    while right < n - 1 and float(smooth_profile[right + 1]) >= floor:
        right += 1
    segment_len = int(max(1, right - left + 1))

    drops = _flank_drops(smooth_profile, left, right, peak_f)
    if drops:
        local_delta = int(round(max(0.0, max(drops))))
    else:
        local_delta = int(round(max(0.0, peak_f - line_med)))

    peak_i = int(round(peak_f))
    background = int(round(line_med))
    prominence = max(0, peak_i - background)

    # Neighbor contrast gate when flanks exist.
    if drops and max(drops) < float(LOCAL_NEIGHBOR_DROP):
        return LineProbeResult(
            False,
            None,
            None,
            peak_i,
            background,
            prominence,
            segment_len,
            int(prominence_min),
            detect_regime="local",
            global_delta=g_delta,
            local_delta=local_delta,
            reliability_score=0.0,
        )

    if local_delta < int(prominence_min):
        return LineProbeResult(
            False,
            None,
            None,
            peak_i,
            background,
            prominence,
            segment_len,
            int(prominence_min),
            detect_regime="local",
            global_delta=g_delta,
            local_delta=local_delta,
            reliability_score=0.0,
        )

    # Fit machine length band when set. len==1 alone is not an extra reject.
    if reflector_len_min is not None and segment_len < int(reflector_len_min):
        return LineProbeResult(
            False,
            None,
            None,
            peak_i,
            background,
            prominence,
            segment_len,
            int(prominence_min),
            detect_regime="local",
            global_delta=g_delta,
            local_delta=local_delta,
            reliability_score=0.0,
        )
    if reflector_len_max is not None and segment_len > int(reflector_len_max):
        return LineProbeResult(
            False,
            None,
            None,
            peak_i,
            background,
            prominence,
            segment_len,
            int(prominence_min),
            detect_regime="local",
            global_delta=g_delta,
            local_delta=local_delta,
            reliability_score=0.0,
        )

    score = reliability_score_for_segment(
        smooth_profile, left, right, peak_f, segment_len, reflector_len_min, reflector_len_max
    )
    return _result_with_centroid(
        found=True,
        smooth_profile=smooth_profile,
        sample_xs=sample_xs,
        sample_ys=sample_ys,
        idx=idx,
        peak=peak_i,
        background=background,
        prominence=prominence,
        segment_len=segment_len,
        prominence_min=int(prominence_min),
        detect_regime="local",
        global_delta=g_delta,
        local_delta=local_delta,
        reliability_score=score,
    )


_ADAPTIVE_FLOOR = 12  # absolute minimum prominence even if scene is flat (noise gate)
_ADAPTIVE_RATIO = 0.5  # require at least 50% of (max - median) — guarantees the actual peak passes
# Reject argmax-on-noise when the 1D profile is almost flat (no real reflector bump).
_MIN_MAX_MEDIAN_SPREAD = 16.0


def _prom_min_and_smooth(
    gray: np.ndarray,
    axis_p0_json: str,
    axis_p1_json: str,
    thickness_px: int,
    threshold_mode: str,
    prominence_min_fixed: int,
    prominence_offset: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int] | LineProbeResult:
    """Sample + smooth + compute prom_min. Returns arrays or an early fail result."""
    h, w = gray.shape[:2]
    p0n = _parse_xy(axis_p0_json)
    p1n = _parse_xy(axis_p1_json)
    p0 = (p0n[0] * w, p0n[1] * h)
    p1 = (p1n[0] * w, p1n[1] * h)

    profile, sx, sy = sample_line_profile_gray(gray, p0, p1, thickness_px=max(1, int(thickness_px)))
    if profile.size < 4:
        return LineProbeResult(False, None, None, 0, 0, 0, 0, 0)

    smooth = _smooth(profile, k=3)
    offset = int(prominence_offset or 0)
    mode = (threshold_mode or "fixed").lower()
    if mode in ("adaptive", "learned"):
        med = float(np.median(smooth))
        peak = float(smooth.max())
        adaptive_prom = max(_ADAPTIVE_FLOOR, int(round((peak - med) * _ADAPTIVE_RATIO)))
        prom_min = max(0, adaptive_prom + offset)
    else:
        prom_min = max(0, int(prominence_min_fixed) + offset)
    return smooth, sx, sy, int(prom_min)


def line_peak_position_gray(
    gray: np.ndarray,
    axis_p0_json: str,
    axis_p1_json: str,
    thickness_px: int,
    threshold_mode: str,
    prominence_min_fixed: int,
    prominence_offset: int = 0,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
    *,
    dual_regime: bool = False,
    prev_regime: str | None = None,
) -> LineProbeResult:
    """Top-level call on a shared grayscale frame (preferred hot path).

    Threshold semantics:
    - mode == "fixed":
          prom_min = max(0, prominence_min_fixed + offset)
    - mode == "adaptive":
          prom_min = max(_ADAPTIVE_FLOOR, round((max - median) * _ADAPTIVE_RATIO)) + offset
      This means: if the scene contains a real peak, the half of its relative
      height is the bar to clear — peak (= 100%) always passes. If the scene
      is flat, _ADAPTIVE_FLOOR rejects ambient noise.
    A hard `max-median` spread check also rejects nearly flat profiles so random
    argmax does not synthesize a reflector.

    When dual_regime is False (default), behavior is the legacy path only —
    no hysteresis / local-cluster code runs.
    """
    prepared = _prom_min_and_smooth(
        gray, axis_p0_json, axis_p1_json, thickness_px, threshold_mode, prominence_min_fixed, prominence_offset
    )
    if isinstance(prepared, LineProbeResult):
        return prepared
    smooth, sx, sy, prom_min = prepared

    line_med = float(np.median(smooth))
    line_peak = float(np.max(smooth))
    global_delta = int(round(max(0.0, line_peak - line_med)))

    if line_peak - line_med < _MIN_MAX_MEDIAN_SPREAD:
        pk_i = int(round(line_peak))
        bg_i = int(round(line_med))
        prom_i = max(0, pk_i - bg_i)
        if dual_regime:
            return LineProbeResult(
                False,
                None,
                None,
                pk_i,
                bg_i,
                prom_i,
                0,
                int(prom_min),
                detect_regime=resolve_detect_regime(prev_regime, global_delta),
                global_delta=global_delta,
                local_delta=0,
                reliability_score=0.0,
            )
        return LineProbeResult(False, None, None, pk_i, bg_i, prom_i, 0, int(prom_min))

    len_min = int(reflector_len_min) if reflector_len_min is not None and int(reflector_len_min) > 0 else None
    len_max = int(reflector_len_max) if reflector_len_max is not None and int(reflector_len_max) > 0 else None

    if not dual_regime:
        return find_peak_on_profile(
            smooth,
            sx,
            sy,
            prominence_min=prom_min,
            reflector_len_min=len_min,
            reflector_len_max=len_max,
        )

    # --- Dual-regime path (flag ON only) ---
    regime = resolve_detect_regime(prev_regime, global_delta)
    use_local = regime == "local"

    if not use_local:
        global_result, overflow = _find_peak_on_profile_core(
            smooth,
            sx,
            sy,
            prominence_min=prom_min,
            reflector_len_min=len_min,
            reflector_len_max=len_max,
            soft_len_max=True,
        )
        if overflow:
            use_local = True
        else:
            # Annotate telemetry; soft reliability score on accept.
            idx = int(np.argmax(smooth))
            peak_f = float(smooth[idx])
            seg_thr = float(global_result.background) + 0.5 * float(global_result.prominence)
            left = idx
            while left > 0 and float(smooth[left - 1]) >= seg_thr:
                left -= 1
            right = idx
            n = int(smooth.size)
            while right < n - 1 and float(smooth[right + 1]) >= seg_thr:
                right += 1
            score = None
            if global_result.found:
                score = reliability_score_for_segment(
                    smooth, left, right, peak_f, global_result.segment_len, len_min, len_max
                )
            drops = _flank_drops(smooth, left, right, peak_f)
            local_d = int(round(max(drops))) if drops else 0
            return LineProbeResult(
                global_result.found,
                global_result.position_01,
                global_result.centroid_px,
                global_result.peak,
                global_result.background,
                global_result.prominence,
                global_result.segment_len,
                global_result.active_threshold,
                detect_regime="global",
                global_delta=global_delta,
                local_delta=local_d,
                reliability_score=score,
            )

    return find_local_bright_cluster(
        smooth,
        sx,
        sy,
        prominence_min=prom_min,
        reflector_len_min=len_min,
        reflector_len_max=len_max,
        global_delta=global_delta,
    )


def line_peak_position(
    frame_bgr: np.ndarray,
    axis_p0_json: str,
    axis_p1_json: str,
    thickness_px: int,
    threshold_mode: str,
    prominence_min_fixed: int,
    prominence_offset: int = 0,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
    *,
    dual_regime: bool = False,
    prev_regime: str | None = None,
) -> LineProbeResult:
    """BGR convenience wrapper — converts to gray then calls line_peak_position_gray."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return line_peak_position_gray(
        gray,
        axis_p0_json,
        axis_p1_json,
        thickness_px,
        threshold_mode,
        prominence_min_fixed,
        prominence_offset=prominence_offset,
        reflector_len_min=reflector_len_min,
        reflector_len_max=reflector_len_max,
        dual_regime=dual_regime,
        prev_regime=prev_regime,
    )

def line_peak_on_smooth_profile(
    smooth: np.ndarray,
    sample_xs: np.ndarray | None = None,
    sample_ys: np.ndarray | None = None,
    *,
    prominence_min: int,
    reflector_len_min: int | None = None,
    reflector_len_max: int | None = None,
    dual_regime: bool = False,
    prev_regime: str | None = None,
) -> LineProbeResult:
    """Test / tooling helper: run peak detection on an already-smoothed 1D profile."""
    n = int(smooth.size)
    if sample_xs is None:
        sample_xs = np.arange(n, dtype=np.float32)
    if sample_ys is None:
        sample_ys = np.zeros(n, dtype=np.float32)
    len_min = int(reflector_len_min) if reflector_len_min is not None and int(reflector_len_min) > 0 else None
    len_max = int(reflector_len_max) if reflector_len_max is not None and int(reflector_len_max) > 0 else None

    line_med = float(np.median(smooth))
    line_peak = float(np.max(smooth))
    global_delta = int(round(max(0.0, line_peak - line_med)))

    if line_peak - line_med < _MIN_MAX_MEDIAN_SPREAD:
        pk_i = int(round(line_peak))
        bg_i = int(round(line_med))
        prom_i = max(0, pk_i - bg_i)
        if dual_regime:
            return LineProbeResult(
                False,
                None,
                None,
                pk_i,
                bg_i,
                prom_i,
                0,
                int(prominence_min),
                detect_regime=resolve_detect_regime(prev_regime, global_delta),
                global_delta=global_delta,
                local_delta=0,
                reliability_score=0.0,
            )
        return LineProbeResult(False, None, None, pk_i, bg_i, prom_i, 0, int(prominence_min))

    if not dual_regime:
        return find_peak_on_profile(
            smooth, sample_xs, sample_ys, prominence_min=prominence_min, reflector_len_min=len_min, reflector_len_max=len_max
        )

    regime = resolve_detect_regime(prev_regime, global_delta)
    use_local = regime == "local"
    if not use_local:
        global_result, overflow = _find_peak_on_profile_core(
            smooth,
            sample_xs,
            sample_ys,
            prominence_min=prominence_min,
            reflector_len_min=len_min,
            reflector_len_max=len_max,
            soft_len_max=True,
        )
        if not overflow:
            idx = int(np.argmax(smooth))
            peak_f = float(smooth[idx])
            seg_thr = float(global_result.background) + 0.5 * float(global_result.prominence)
            left = idx
            while left > 0 and float(smooth[left - 1]) >= seg_thr:
                left -= 1
            right = idx
            while right < n - 1 and float(smooth[right + 1]) >= seg_thr:
                right += 1
            score = None
            if global_result.found:
                score = reliability_score_for_segment(
                    smooth, left, right, peak_f, global_result.segment_len, len_min, len_max
                )
            drops = _flank_drops(smooth, left, right, peak_f)
            local_d = int(round(max(drops))) if drops else 0
            return LineProbeResult(
                global_result.found,
                global_result.position_01,
                global_result.centroid_px,
                global_result.peak,
                global_result.background,
                global_result.prominence,
                global_result.segment_len,
                global_result.active_threshold,
                detect_regime="global",
                global_delta=global_delta,
                local_delta=local_d,
                reliability_score=score,
            )
    return find_local_bright_cluster(
        smooth,
        sample_xs,
        sample_ys,
        prominence_min=prominence_min,
        reflector_len_min=len_min,
        reflector_len_max=len_max,
        global_delta=global_delta,
    )
