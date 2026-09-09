"""Synthetic position-series generators for Schmitt counter tests.

Everything is a list of ``(t_seconds, pos)`` samples at a fixed frame period so
the counter's injected clock is deterministic. ``pos`` is the normalized line
position (0..1) the line probe would report.
"""

from __future__ import annotations

import random

from app.vision.schmitt_counter import SchmittCounter


class Seq:
    """Fluent builder for a timed position series."""

    def __init__(self, period: float = 0.1, start_t: float = 0.0) -> None:
        self.period = period
        self.t = start_t
        self.pts: list[tuple[float, float | None]] = []

    def _push(self, v: float | None) -> None:
        if v is None:
            self.pts.append((round(self.t, 4), None))
        else:
            self.pts.append((round(self.t, 4), max(0.0, min(1.0, v))))
        self.t += self.period

    def hold(self, v: float | None, n: int) -> "Seq":
        for _ in range(n):
            self._push(v)
        return self

    def ramp(self, a: float, b: float, n: int) -> "Seq":
        for i in range(1, n + 1):
            self._push(a + (b - a) * i / n)
        return self

    def jitter(self, center: float, amp: float, n: int, seed: int = 0) -> "Seq":
        rng = random.Random(seed)
        for _ in range(n):
            self._push(center + rng.uniform(-amp, amp))
        return self

    def stroke(
        self,
        open_pos: float,
        closed_pos: float,
        *,
        ramp: int = 6,
        closed_dwell: int = 3,
        open_dwell: int = 3,
    ) -> "Seq":
        """One open -> closed -> open excursion (exactly one real cycle)."""
        self.ramp(open_pos, closed_pos, ramp)
        self.hold(closed_pos, closed_dwell)
        self.ramp(closed_pos, open_pos, ramp)
        self.hold(open_pos, open_dwell)
        return self

    def strokes(
        self,
        open_pos: float,
        closed_pos: float,
        n: int,
        *,
        ramp: int = 6,
        closed_dwell: int = 3,
        open_dwell: int = 3,
    ) -> "Seq":
        self.hold(open_pos, open_dwell)
        for _ in range(n):
            self.stroke(
                open_pos, closed_pos,
                ramp=ramp, closed_dwell=closed_dwell, open_dwell=open_dwell,
            )
        return self

    def build(self) -> list[tuple[float, float | None]]:
        return list(self.pts)


def run(counter: SchmittCounter, samples: list[tuple[float, float | None]]) -> dict:
    """Feed a series through the counter and collect observable results."""
    emits: list[float] = []
    for t, pos in samples:
        dt = counter.step(pos, t)
        if dt is not None and dt > 0.0:
            emits.append(round(float(dt), 4))
    kinds = [e.kind for e in counter.learn_events]
    return {
        "count": counter.count,
        "emits": emits,
        "final_ref": counter.closed_ref,
        "learn_kinds": kinds,
        "learn_events": list(counter.learn_events),
    }
