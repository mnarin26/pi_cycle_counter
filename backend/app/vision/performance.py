"""Simple FPS / timing helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PerformanceMonitor:
    process_ms_ema: float = 0.0
    _last: float = field(default_factory=lambda: time.monotonic())

    def tick_process(self, elapsed_s: float) -> None:
        ms = elapsed_s * 1000.0
        self.process_ms_ema = self.process_ms_ema * 0.9 + ms * 0.1
