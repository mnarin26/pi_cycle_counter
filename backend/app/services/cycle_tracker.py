"""Track full OPEN/CLOSED/OPEN (or reverse) cycles.

Wait zones come from the clamp state machine. A cycle is wait(A) -> wait(B)
-> wait(A) where A/B are OPEN/CLOSED. Stroke reversal is handled in the
state machine (immediate wait on direction change), not here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

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
    _zone_seq: list[str] = field(default_factory=list)
    last_emit: dict | None = None
    unknown_grace_s: float = 3.0
    unknown_grace_after_extreme_s: float = 12.0
    min_travel_range: float = 0.18

    def _reset_cycle(self) -> None:
        self._t_cycle_start = None
        self._cycle_start_zone = ConfirmedZone.UNKNOWN
        self._seen_opposite_zone = False
        self._last_wait_zone = ConfirmedZone.UNKNOWN
        self._unknown_since = None
        self._pos_min = None
        self._pos_max = None
        self._zone_seq = []

    def _reset_travel(self) -> None:
        self._pos_min = None
        self._pos_max = None

    def _note_extrema(self, position_01: float | None) -> None:
        if position_01 is None or self._t_cycle_start is None:
            return
        p = float(position_01)
        if self._pos_min is None:
            self._pos_min = self._pos_max = p
        else:
            self._pos_min = min(self._pos_min, p)
            self._pos_max = max(self._pos_max, p)

    def _note_zone(self, label: str) -> None:
        if not label:
            return
        if self._zone_seq and self._zone_seq[-1] == label:
            return
        self._zone_seq.append(label)
        if len(self._zone_seq) > 40:
            self._zone_seq = self._zone_seq[-40:]

    def _unknown_grace_limit(self) -> float:
        if self._seen_opposite_zone:
            return max(self.unknown_grace_s, self.unknown_grace_after_extreme_s)
        return self.unknown_grace_s

    def _snapshot_emit(self, z: ConfirmedZone) -> None:
        self.last_emit = {
            "start_zone": self._cycle_start_zone.value,
            "end_zone": z.value,
            "pos_min": self._pos_min,
            "pos_max": self._pos_max,
            "travel": (
                None
                if self._pos_min is None or self._pos_max is None
                else round(self._pos_max - self._pos_min, 4)
            ),
            "seen_opposite": True,
            "zone_seq": list(self._zone_seq),
        }

    def _apply_wait_zone(self, z: ConfirmedZone, position_01: float | None) -> float | None:
        now = time.monotonic()
        if z == self._last_wait_zone:
            return None

        if self._cycle_start_zone not in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
            self._cycle_start_zone = z
            self._t_cycle_start = now
            self._seen_opposite_zone = False
            self._zone_seq = [z.value]
            self._reset_travel()
            self._note_extrema(position_01)
            self._last_wait_zone = z
            return None

        if z != self._cycle_start_zone:
            self._seen_opposite_zone = True
            self._last_wait_zone = z
            return None

        if self._seen_opposite_zone and self._t_cycle_start is not None:
            dt = now - self._t_cycle_start
            self._snapshot_emit(z)
            self._t_cycle_start = now
            self._seen_opposite_zone = False
            self._zone_seq = [z.value]
            self._reset_travel()
            self._note_extrema(position_01)
            self._last_wait_zone = z
            return max(0.0, dt)

        self._t_cycle_start = now
        self._reset_travel()
        self._note_extrema(position_01)
        self._last_wait_zone = z
        return None

    def on_confirmed(self, z: ConfirmedZone, position_01: float | None = None) -> float | None:
        """
        Returns cycle duration in seconds only for full cycles:
        wait(A) -> wait(B) -> wait(A), where A/B are OPEN/CLOSED.
        """
        now = time.monotonic()
        self._last_confirmed = z
        self._note_extrema(position_01)

        if z != ConfirmedZone.UNKNOWN:
            self._note_zone(z.value)
        elif self._t_cycle_start is not None:
            self._note_zone("UNKNOWN")

        if z == ConfirmedZone.MOVING:
            self._unknown_since = None
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
            return self._apply_wait_zone(z, position_01)

        return None
