"""Track CLOSED -> OPEN confirmed transitions and cycle duration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.vision.state_machine import ConfirmedZone


@dataclass
class CycleTracker:
    _t_closed_enter: float | None = None
    _last_confirmed: ConfirmedZone = ConfirmedZone.UNKNOWN

    def on_confirmed(self, z: ConfirmedZone) -> float | None:
        """
        Returns cycle duration in seconds when transition CLOSED -> OPEN is completed.
        """
        now = time.monotonic()
        prev = self._last_confirmed
        self._last_confirmed = z

        if z == ConfirmedZone.CLOSED:
            self._t_closed_enter = now
            return None

        if z == ConfirmedZone.OPEN and prev == ConfirmedZone.CLOSED and self._t_closed_enter is not None:
            dt = now - self._t_closed_enter
            self._t_closed_enter = None
            return max(0.0, dt)

        if z != ConfirmedZone.CLOSED:
            if z != ConfirmedZone.MOVING:
                self._t_closed_enter = None
        return None
