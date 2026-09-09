"""Lightweight host metrics for diag / process log (no psutil).

Reads /proc and thermal sysfs — cheap enough for ~1 Hz cache.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
_LOADAVG = Path("/proc/loadavg")
_MEMINFO = Path("/proc/meminfo")
_STAT = Path("/proc/stat")


@dataclass(frozen=True)
class HostMetrics:
    cpu_pct: float | None = None
    temp_c: float | None = None
    load1: float | None = None
    mem_pct: float | None = None
    mono: float = 0.0

    def as_csv_fields(self) -> str:
        """Semicolon fields: cpu_pct;temp_c;load1;mem_pct (empty if unknown)."""

        def f(v: float | None, nd: int = 1) -> str:
            if v is None:
                return ""
            return f"{v:.{nd}f}"

        return f"{f(self.cpu_pct)};{f(self.temp_c)};{f(self.load1, 2)};{f(self.mem_pct)}"

    def as_dict(self) -> dict:
        return {
            "cpu_pct": self.cpu_pct,
            "temp_c": self.temp_c,
            "load1": self.load1,
            "mem_pct": self.mem_pct,
        }


class HostSampler:
    """Cached host sample; call ``get()`` freely — refreshes at most every ``min_interval_s``."""

    def __init__(self, min_interval_s: float = 1.0):
        self.min_interval_s = max(0.5, float(min_interval_s))
        self._last: HostMetrics = HostMetrics()
        self._prev_idle: int | None = None
        self._prev_total: int | None = None
        self._last_mono: float = 0.0

    def get(self, *, force: bool = False) -> HostMetrics:
        now = time.monotonic()
        if (
            not force
            and self._last_mono
            and (now - self._last_mono) < self.min_interval_s
        ):
            return self._last
        self._last = self._sample(now)
        self._last_mono = now
        return self._last

    def _sample(self, mono: float) -> HostMetrics:
        temp_c = self._read_temp_c()
        load1 = self._read_load1()
        mem_pct = self._read_mem_pct()
        cpu_pct = self._read_cpu_pct()
        return HostMetrics(
            cpu_pct=cpu_pct,
            temp_c=temp_c,
            load1=load1,
            mem_pct=mem_pct,
            mono=mono,
        )

    @staticmethod
    def _read_temp_c() -> float | None:
        try:
            raw = _THERMAL.read_text(encoding="ascii").strip()
            # millidegrees Celsius on Pi / most Linux boards
            return int(raw) / 1000.0
        except Exception:
            return None

    @staticmethod
    def _read_load1() -> float | None:
        try:
            return float(_LOADAVG.read_text(encoding="ascii").split()[0])
        except Exception:
            return None

    @staticmethod
    def _read_mem_pct() -> float | None:
        try:
            total = avail = None
            for line in _MEMINFO.read_text(encoding="ascii").splitlines():
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
                if total is not None and avail is not None:
                    break
            if not total or avail is None:
                return None
            used = max(0, total - avail)
            return 100.0 * used / total
        except Exception:
            return None

    def _read_cpu_pct(self) -> float | None:
        try:
            line = _STAT.read_text(encoding="ascii").splitlines()[0]
            parts = line.split()
            # cpu user nice system idle iowait irq softirq steal ...
            nums = [int(x) for x in parts[1:8]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            if self._prev_total is None or self._prev_idle is None:
                self._prev_idle = idle
                self._prev_total = total
                return None  # need two samples
            d_total = total - self._prev_total
            d_idle = idle - self._prev_idle
            self._prev_idle = idle
            self._prev_total = total
            if d_total <= 0:
                return None
            busy = max(0.0, 1.0 - (d_idle / d_total))
            return 100.0 * busy
        except Exception:
            return None


# Process-wide sampler (orchestrator + admin can share via import)
_default_sampler = HostSampler(min_interval_s=1.0)


def sample_host(*, force: bool = False) -> HostMetrics:
    return _default_sampler.get(force=force)
