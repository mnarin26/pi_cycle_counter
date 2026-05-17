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

import json
from dataclasses import dataclass

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


def _parse_xy(s: str) -> tuple[float, float]:
    arr = json.loads(s)
    return float(arr[0]), float(arr[1])


def sample_line_profile(
    frame_bgr: np.ndarray,
    p0_xy: tuple[float, float],
    p1_xy: tuple[float, float],
    thickness_px: int = 7,
    num_samples: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a 1D brightness profile along the line p0 -> p1.

    For each of N points along the line, we max-pool the grayscale values in
    a perpendicular window of `thickness_px` total pixels (clamped to frame).

    Returns:
        profile: shape (N,) uint8
        sample_xs: shape (N,) float32  -- x pixel coord of each sample center
        sample_ys: shape (N,) float32  -- y pixel coord of each sample center
    """
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    ax, ay = p0_xy
    bx, by = p1_xy
    dx, dy = bx - ax, by - ay
    length = float(np.hypot(dx, dy))
    if length < 2.0:
        return np.zeros(2, dtype=np.uint8), np.array([ax, bx], dtype=np.float32), np.array([ay, by], dtype=np.float32)

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
    sampled = cv2.remap(gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # sampled shape == (len(offsets), n); collapse perpendicular axis via max-pool.
    profile = sampled.max(axis=0).astype(np.uint8)
    return profile, cx.astype(np.float32), cy.astype(np.float32)


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
    n = smooth_profile.size
    if n < 4:
        return LineProbeResult(False, None, None, 0, 0, 0, 0, prominence_min)

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
        return LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min))

    if reflector_len_min is not None and segment_len < int(reflector_len_min):
        return LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min))
    if reflector_len_max is not None and segment_len > int(reflector_len_max):
        return LineProbeResult(False, None, None, peak, background, prominence, segment_len, int(prominence_min))

    refined = _parabolic_subpixel(smooth_profile.astype(np.uint8), idx)
    refined = float(np.clip(refined, 0.0, n - 1))
    position_01 = refined / float(n - 1)

    lo = int(np.floor(refined))
    hi = min(n - 1, lo + 1)
    frac = refined - lo
    cx = float(sample_xs[lo]) * (1.0 - frac) + float(sample_xs[hi]) * frac
    cy = float(sample_ys[lo]) * (1.0 - frac) + float(sample_ys[hi]) * frac

    return LineProbeResult(True, position_01, (cx, cy), peak, background, prominence, segment_len, int(prominence_min))


_ADAPTIVE_FLOOR = 8  # absolute minimum prominence even if scene is flat (noise gate)
_ADAPTIVE_RATIO = 0.5  # require at least 50% of (max - median) — guarantees the actual peak passes


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
) -> LineProbeResult:
    """Top-level call used by the orchestrator.

    Threshold semantics:
    - mode == "fixed":
          prom_min = max(0, prominence_min_fixed + offset)
    - mode == "adaptive":
          prom_min = max(_ADAPTIVE_FLOOR, round((max - median) * _ADAPTIVE_RATIO)) + offset
      This means: if the scene contains a real peak, the half of its relative
      height is the bar to clear — peak (= 100%) always passes. If the scene
      is flat, _ADAPTIVE_FLOOR rejects ambient noise.
    """
    h, w = frame_bgr.shape[:2]
    p0n = _parse_xy(axis_p0_json)
    p1n = _parse_xy(axis_p1_json)
    p0 = (p0n[0] * w, p0n[1] * h)
    p1 = (p1n[0] * w, p1n[1] * h)

    profile, sx, sy = sample_line_profile(frame_bgr, p0, p1, thickness_px=max(1, int(thickness_px)))
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

    len_min = int(reflector_len_min) if reflector_len_min is not None and int(reflector_len_min) > 0 else None
    len_max = int(reflector_len_max) if reflector_len_max is not None and int(reflector_len_max) > 0 else None
    return find_peak_on_profile(
        smooth,
        sx,
        sy,
        prominence_min=prom_min,
        reflector_len_min=len_min,
        reflector_len_max=len_max,
    )
