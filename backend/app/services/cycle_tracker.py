"""Track full OPEN/CLOSED/OPEN (or reverse) cycles.

In direction-based state mode, the first stable wait zone may be OPEN or
CLOSED depending on camera axis direction. To avoid missing counts, we
measure cycle only when the machine returns to the same wait zone after
visiting the opposite zone:

    OPEN -> CLOSED -> OPEN  (or CLOSED -> OPEN -> CLOSED)
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

    def on_confirmed(self, z: ConfirmedZone) -> float | None:
        """
        Returns cycle duration in seconds only for full cycles:
        wait(A) -> wait(B) -> wait(A), where A/B are OPEN/CLOSED.
        """
        now = time.monotonic()
        self._last_confirmed = z

        if z in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
            # Ignore repeated confirmations while machine stays in same wait zone.
            if z == self._last_wait_zone:
                return None

            if self._cycle_start_zone not in (ConfirmedZone.OPEN, ConfirmedZone.CLOSED):
                self._cycle_start_zone = z
                self._t_cycle_start = now
                self._seen_opposite_zone = False
            elif z != self._cycle_start_zone:
                self._seen_opposite_zone = True
            elif self._seen_opposite_zone and self._t_cycle_start is not None:
                dt = now - self._t_cycle_start
                self._t_cycle_start = now
                self._seen_opposite_zone = False
                self._last_wait_zone = z
                return max(0.0, dt)
            else:
                # Defensive re-anchor if we re-enter start zone without opposite.
                self._t_cycle_start = now

            self._last_wait_zone = z
            return None

        if z not in (ConfirmedZone.MOVING,):
            self._t_cycle_start = None
            self._cycle_start_zone = ConfirmedZone.UNKNOWN
            self._seen_opposite_zone = False
            self._last_wait_zone = ConfirmedZone.UNKNOWN
        return None
