"""Track full OPEN/CLOSED/OPEN (or reverse) cycles.

In direction-based state mode, the first stable wait zone may be OPEN or
CLOSED depending on camera axis direction. To avoid missing counts, we
measure cycle only when the machine returns to the same wait zone after
visiting the opposite zone:

    OPEN -> CLOSED -> OPEN  (or CLOSED -> OPEN -> CLOSED)

When the axis is shortened (reflector hidden under panel at full open/close),
an endpoint visit is inferred from travel along the visible axis segment.

Brief UNKNOWN flickers must not reset an in-progress cycle; sustained UNKNOWN
clears state (longer tolerance after an inferred endpoint visit).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.vision.state_machine import ConfirmedZone


@dataclass
class CycleTracker:
    _t_cycle_start: float | None = None
    _cycle_start_zone: ConfirmedZone = ConfirmedZone.UNKNOWN
    _seen_opposite_zone: bool = False
    _last_wait_zone: ConfirmedZone = ConfirmedZone.UNKNOWN
    _last_confirmed: ConfirmedZone = ConfirmedZone.UNKNOWN
    _unknown_since: float | None = None
    _pos_min: float | None = None
    _pos_max: float | None = None
    unknown_grace_s: float = 3.0
    unknown_grace_after_extreme_s: float = 12.0
    endpoint_margin: float = 0.15
    min_travel_range: float = 0.18

    def _reset_cycle(self) -> None:
        self._t_cycle_start = None
        self._cycle_start_zone = ConfirmedZone.UNKNOWN
        self._seen_opposite_zone = False
        self._last_wait_zone = ConfirmedZone.UNKNOWN
        self._unknown_since = None
        self._pos_min = None
        self._pos_max = None

    def _reset_travel(self) -> None:
        self._pos_min = None
        self._pos_max = None

    def _note_position(self, position_01: float | None) -> None:
        if position_01 is None or self._t_cycle_start is None:
            return
        p = float(position_01)
        if self._pos_min is None:
            self._pos_min = self._pos_max = p
        else:
            self._pos_min = min(self._pos_min, p)
            self._pos_max = max(self._pos_max, p)
        self._maybe_mark_endpoint_visit()

    def _maybe_mark_endpoint_visit(self) -> None:
        if self._seen_opposite_zone or self._pos_min is None or self._pos_max is None:
            return
        if self._pos_max - self._pos_min < self.min_travel_range:
            return
        lo = self._pos_min <= self.endpoint_margin
        hi = self._pos_max >= (1.0 - self.endpoint_margin)
        if lo or hi:
            self._seen_opposite_zone = True

    def _unknown_grace_limit(self) -> float:
        if self._seen_opposite_zone:
            return max(self.unknown_grace_s, self.unknown_grace_after_extreme_s)
        return self.unknown_grace_s

    def on_confirmed(self, z: ConfirmedZone, position_01: float | None = None) -> float | None:
        """
        Returns cycle duration in seconds only for full cycles:
        wait(A) -> wait(B) -> wait(A), where A/B are OPEN/CLOSED or an
        inferred endpoint visit on a shortened axis.
        """
        now = time.monotonic()
        self._last_confirmed = z
        self._note_position(position_01)

        if z == ConfirmedZone.MOVING:
            self._unknown_since = None
            # Left dwell without a confirmed opposite zone (shortened axis path).
            self._last_wait_zone = ConfirmedZone.UNKNOWN
            return None

        if z == ConfirmedZone.UNKNOWN:
            if self._t_cycle_start is None:
                return None
            if self._unknown_since is None:
                self._unknown_since = now
            elif now - self._unknown_since > self._unknown_grace_limit():
                self._reset_cycle()
            return None

        if z in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
            self._unknown_since = None
            if z == self._last_wait_zone:
                return None

            if self._cycle_start_zone not in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
                self._cycle_start_zone = z
                self._t_cycle_start = now
                self._seen_opposite_zone = False
                self._reset_travel()
                self._note_position(position_01)
            elif z != self._cycle_start_zone:
                self._seen_opposite_zone = True
            elif self._seen_opposite_zone and self._t_cycle_start is not None:
                dt = now - self._t_cycle_start
                self._t_cycle_start = now
                self._seen_opposite_zone = False
                self._reset_travel()
                self._note_position(position_01)
                self._last_wait_zone = z
                return max(0.0, dt)
            else:
                self._t_cycle_start = now
                self._reset_travel()
                self._note_position(position_01)

            self._last_wait_zone = z
            return None

        return None
