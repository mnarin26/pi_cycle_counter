"""Ring buffer of recent per-machine samples (~30s at ~10Hz)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass
class Sample:
    t: float
    machine_id: int
    position_01: float | None
    state: str


class PlaybackBuffer:
    def __init__(self, max_seconds: float = 30.0, max_samples_per_machine: int = 600):
        self.max_seconds = max_seconds
        self.max_samples_per_machine = max_samples_per_machine
        self._lock = Lock()
        self._by_machine: dict[int, deque[Sample]] = {}

    def push(self, machine_id: int, position_01: float | None, state: str) -> None:
        now = time.monotonic()
        with self._lock:
            dq = self._by_machine.setdefault(machine_id, deque())
            dq.append(Sample(now, machine_id, position_01, state))
            while dq and now - dq[0].t > self.max_seconds:
                dq.popleft()
            while len(dq) > self.max_samples_per_machine:
                dq.popleft()

    def get_machine(self, machine_id: int) -> list[dict]:
        with self._lock:
            dq = self._by_machine.get(machine_id)
            if not dq:
                return []
            return [{"t": s.t, "position_01": s.position_01, "state": s.state} for s in dq]
