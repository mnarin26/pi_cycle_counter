"""Motion + dwell based state machine with jitter tolerance."""

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
    _min_seen: float = 0.15
    _max_seen: float = 0.85
    _filtered_pos: float | None = None
    _confirmed: ConfirmedZone = ConfirmedZone.UNKNOWN

    def reset(self) -> None:
        self._last_pos = None
        self._last_ms = 0.0
        self._moving_raw = False
        self._moving_since_ms = 0.0
        self._still_since_ms = 0.0
        self._min_seen = min(self.cfg.closed_1d, self.cfg.open_1d)
        self._max_seen = max(self.cfg.closed_1d, self.cfg.open_1d)
        self._filtered_pos = None
        self._confirmed = ConfirmedZone.UNKNOWN

    def _classify_wait_zone(self, pos: float) -> ConfirmedZone:
        # Dynamic endpoints learn from running range; fallback to configured ends.
        span = self._max_seen - self._min_seen
        if span < max(0.06, self.cfg.hysteresis):
            open_ref = self.cfg.open_1d
            closed_ref = self.cfg.closed_1d
        else:
            open_ref = self._max_seen
            closed_ref = self._min_seen

        d_open = abs(pos - open_ref)
        d_closed = abs(pos - closed_ref)
        margin = max(0.01, self.cfg.hysteresis * 0.35)

        if d_open + margin < d_closed:
            return ConfirmedZone.OPEN
        if d_closed + margin < d_open:
            return ConfirmedZone.CLOSED
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
            # Low-pass to avoid hand jitter / sensor noise.
            self._filtered_pos = self._filtered_pos * 0.72 + pos * 0.28
        fpos = self._filtered_pos

        if self._last_pos is None:
            self._last_pos = fpos
            self._last_ms = now_ms
            self._still_since_ms = now_ms
            self._moving_since_ms = now_ms
            self._min_seen = min(self._min_seen, fpos)
            self._max_seen = max(self._max_seen, fpos)
            return self._confirmed

        dt = max(1.0, now_ms - self._last_ms)
        dpos = abs(fpos - self._last_pos)
        # Movement epsilon is tied to hysteresis so jitter does not trigger MOVING.
        move_eps = max(0.0015, self.cfg.hysteresis * 0.12)
        is_moving_now = dpos > move_eps

        self._last_pos = fpos
        self._last_ms = now_ms
        self._min_seen = min(self._min_seen, fpos)
        self._max_seen = max(self._max_seen, fpos)

        if is_moving_now:
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
            self._confirmed = self._classify_wait_zone(fpos)
        return self._confirmed
