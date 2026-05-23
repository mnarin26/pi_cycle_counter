"""Motion + dwell based state machine with jitter tolerance.

This revision intentionally avoids fixed OPEN/CLOSED position thresholds.
Instead, it models the clamp as a sequence of:

    moving -> waiting -> reverse moving -> waiting

and labels waiting zones by *last movement direction*:
- wait after +direction movement => CLOSED wait
- wait after -direction movement => OPEN wait

This matches molds where absolute end positions drift by product size, but the
two-direction movement pattern remains stable.
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


@dataclass
class StateMachineConfig:
    open_1d: float = 0.85
    closed_1d: float = 0.15
    hysteresis: float = 0.06
    debounce_ms: int = 80
    stability_confirm_ms: int = 500


@dataclass
class ClampStateMachine:
    cfg: StateMachineConfig = field(default_factory=StateMachineConfig)
    _last_pos: float | None = None
    _last_ms: float = 0.0
    _moving_raw: bool = False
    _moving_since_ms: float = 0.0
    _still_since_ms: float = 0.0
    _last_move_dir: int = 0  # +1 or -1 (last meaningful movement direction)
    _filtered_pos: float | None = None
    _confirmed: ConfirmedZone = ConfirmedZone.UNKNOWN

    def reset(self) -> None:
        self._last_pos = None
        self._last_ms = 0.0
        self._moving_raw = False
        self._moving_since_ms = 0.0
        self._still_since_ms = 0.0
        self._last_move_dir = 0
        self._filtered_pos = None
        self._confirmed = ConfirmedZone.UNKNOWN

    def _classify_wait_zone(self) -> ConfirmedZone:
        # No fixed absolute endpoints: zone is inferred from the last movement direction.
        # +dir wait => CLOSED, -dir wait => OPEN.
        if self._last_move_dir > 0:
            return ConfirmedZone.CLOSED
        if self._last_move_dir < 0:
            return ConfirmedZone.OPEN
        return self._confirmed if self._confirmed in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED) else ConfirmedZone.UNKNOWN

    def step(self, position_01: float | None, now_ms: float | None = None) -> ConfirmedZone:
        if now_ms is None:
            now_ms = time.monotonic() * 1000.0

        if position_01 is None:
            self._last_pos = None
            self._filtered_pos = None
            return self._confirmed

        pos = max(0.0, min(1.0, float(position_01)))
        if self._filtered_pos is None:
            self._filtered_pos = pos
        else:
            # Low-pass to avoid jitter, but keep latency low for OPEN/CLOSED
            # confirmation in fast molds.
            self._filtered_pos = self._filtered_pos * 0.45 + pos * 0.55
        fpos = self._filtered_pos

        if self._last_pos is None:
            self._last_pos = fpos
            self._last_ms = now_ms
            self._still_since_ms = now_ms
            self._moving_since_ms = now_ms
            return self._confirmed

        dt = max(1.0, now_ms - self._last_ms)
        delta = fpos - self._last_pos
        dpos = abs(delta)
        # Movement epsilon is tied to hysteresis so jitter does not trigger MOVING.
        move_eps = max(0.0015, self.cfg.hysteresis * 0.12)
        is_moving_now = dpos > move_eps

        self._last_pos = fpos
        self._last_ms = now_ms

        if is_moving_now:
            dir_now = 1 if delta > 0 else -1
            self._last_move_dir = dir_now
            if not self._moving_raw:
                self._moving_raw = True
                self._moving_since_ms = now_ms
            if now_ms - self._moving_since_ms >= self.cfg.debounce_ms:
                self._confirmed = ConfirmedZone.MOVING
            self._still_since_ms = now_ms
            return self._confirmed

        if self._moving_raw:
            self._moving_raw = False
            self._still_since_ms = now_ms

        if now_ms - self._still_since_ms >= self.cfg.stability_confirm_ms:
            self._confirmed = self._classify_wait_zone()
        return self._confirmed
