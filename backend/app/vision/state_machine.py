"""Peak / trough (zig-zag) motion state machine.

Human-perception model, identical for every machine:

- The reflector position (0..1) rides between two ends. p0 is the CLOSED end
  (low), p1 is the OPEN end (high). So a signal *peak* means OPEN and a
  *trough* means CLOSED.
- A swing is only real if it is at least `min_prominence` tall. Anything
  smaller (light flicker, park jitter ~0.03) never flips direction, so it is
  ignored the same way a person ignores it by eye.
- Teleports (steep vertical jumps, e.g. light glints) are held: if |Δpos|
  >= `jump_abs` the previous position is kept (global; not per-machine).
- No absolute OPEN/CLOSED thresholds and no learning: the detector follows
  whatever peak / trough levels the signal actually reaches. When a mold change
  shifts the max, the min, or both, the next peaks/troughs are simply taken at
  the new levels.

The confirmed peaks/troughs are emitted as OPEN / CLOSED to the existing
CycleTracker, which counts a full cycle as A -> B -> A. A peak/trough is only
confirmed once the signal has retraced `min_prominence` away from it, so a
count may land one retrace late -- that is intentional (accuracy over
immediacy).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ConfirmedZone(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MOVING = "MOVING"
    UNKNOWN = "UNKNOWN"


# Frame-to-frame move (on the median-filtered signal) above which the live
# dot shows MOVING. Display only; never affects the cycle count.
_DISPLAY_MOVE_EPS = 0.012


@dataclass
class StateMachineConfig:
    # Legacy/vestigial: kept for config + admin UI compat. Not used by counting.
    min_change: float = 0.006
    # Swing amplitude a peak/trough must have to be counted. Global default.
    min_prominence: float = 0.12
    # Absolute teleport gate (global). Matches shadow zigzag_counter.
    jump_abs: float = 0.30
    # Kept for config/wiring backward-compat; unused by the peak/trough core.
    min_travel: float = 0.18
    debounce_ms: int = 80
    stability_confirm_ms: int = 500


@dataclass
class ClampStateMachine:
    cfg: StateMachineConfig = field(default_factory=StateMachineConfig)

    _win: list[float] = field(default_factory=list)  # last raw samples for median-3
    _dir: int = 0  # 0 unknown, +1 rising (toward OPEN), -1 falling (toward CLOSED)
    _ext: float | None = None  # running extreme of the current leg
    _hi: float | None = None  # unknown-phase running max
    _lo: float | None = None  # unknown-phase running min
    _prev_f: float | None = None  # previous filtered sample (display motion)
    _last_held: float | None = None  # jump-hold latch
    _peak_level: float | None = None  # last confirmed peak (OPEN) level
    _trough_level: float | None = None  # last confirmed trough (CLOSED) level
    _confirmed: ConfirmedZone = ConfirmedZone.UNKNOWN  # last counting zone
    display_zone: ConfirmedZone = ConfirmedZone.UNKNOWN  # lag-free UI hint

    def reset(self) -> None:
        self._win = []
        self._dir = 0
        self._ext = None
        self._hi = None
        self._lo = None
        self._prev_f = None
        self._last_held = None
        self._peak_level = None
        self._trough_level = None
        self._confirmed = ConfirmedZone.UNKNOWN
        self.display_zone = ConfirmedZone.UNKNOWN

    def _prominence(self) -> float:
        return max(0.02, float(self.cfg.min_prominence or 0.06))

    def _jump_abs(self) -> float:
        return max(0.05, float(getattr(self.cfg, "jump_abs", 0.30) or 0.30))

    def _hold_jumps(self, pos: float) -> float:
        if self._last_held is None:
            self._last_held = pos
            return pos
        if abs(pos - self._last_held) >= self._jump_abs():
            return self._last_held
        self._last_held = pos
        return pos

    def _median3(self, pos: float) -> float:
        self._win.append(pos)
        if len(self._win) > 3:
            self._win.pop(0)
        return sorted(self._win)[len(self._win) // 2]

    def _update_display(self, f: float) -> None:
        moving = self._prev_f is not None and abs(f - self._prev_f) >= _DISPLAY_MOVE_EPS
        if moving:
            self.display_zone = ConfirmedZone.MOVING
        elif self._peak_level is not None and self._trough_level is not None:
            mid = 0.5 * (self._peak_level + self._trough_level)
            self.display_zone = ConfirmedZone.OPEN if f >= mid else ConfirmedZone.CLOSED
        elif self._confirmed in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
            self.display_zone = self._confirmed
        else:
            self.display_zone = ConfirmedZone.UNKNOWN

    def step(self, position_01: float | None, now_ms: float | None = None) -> ConfirmedZone:
        if position_01 is None:
            # Signal lost: report UNKNOWN so the tracker's grace/reset applies.
            # Internal peak/trough state is kept (orchestrator resets on long loss).
            return ConfirmedZone.UNKNOWN

        pos = max(0.0, min(1.0, float(position_01)))
        pos = self._hold_jumps(pos)
        f = self._median3(pos)
        prom = self._prominence()

        emitted: ConfirmedZone | None = None

        if self._dir == 0:
            self._hi = f if self._hi is None else max(self._hi, f)
            self._lo = f if self._lo is None else min(self._lo, f)
            if self._hi is not None and f <= self._hi - prom:
                # Fell from a high -> that high was a peak (OPEN).
                self._peak_level = self._hi
                self._dir = -1
                self._ext = f
                emitted = ConfirmedZone.OPEN
            elif self._lo is not None and f >= self._lo + prom:
                # Rose from a low -> that low was a trough (CLOSED).
                self._trough_level = self._lo
                self._dir = 1
                self._ext = f
                emitted = ConfirmedZone.CLOSED
        elif self._dir > 0:
            if self._ext is None or f > self._ext:
                self._ext = f
            if f <= self._ext - prom:
                self._peak_level = self._ext
                self._dir = -1
                self._ext = f
                emitted = ConfirmedZone.OPEN
        else:  # self._dir < 0
            if self._ext is None or f < self._ext:
                self._ext = f
            if f >= self._ext + prom:
                self._trough_level = self._ext
                self._dir = 1
                self._ext = f
                emitted = ConfirmedZone.CLOSED

        self._update_display(f)
        self._prev_f = f

        if emitted is not None:
            self._confirmed = emitted

        # Counting signal is confirmation-only: a fresh OPEN/CLOSED at each
        # confirmed peak/trough, otherwise the held zone. It never returns
        # MOVING, so the cycle count is fully independent of min_change / the
        # display path (MOVING lives only in display_zone for the UI).
        return self._confirmed
