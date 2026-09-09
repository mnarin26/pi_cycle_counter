"""Lightweight per-machine process log (no images).

Writes only inside an optional wall-clock window:
  diag_from <= now < diag_until

If diag_from is empty, logging may start immediately once diag_until is in the future.

Tick rows also record host CPU / temp / load / RAM (cheap /proc + thermal).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.services.host_metrics import sample_host
from app.services.time_windows import to_istanbul

logger = logging.getLogger(__name__)


def parse_diag_dt(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo

        dt = dt.replace(tzinfo=ZoneInfo("Europe/Istanbul"))
    return dt.astimezone(timezone.utc)


CSV_HEADER = "t_iso;mono;kind;state;pos;age_ms;detail;cpu_pct;temp_c;load1;mem_pct"
_FLUSH_EVERY_LINES = 25
_FLUSH_EVERY_S = 2.0
_DEFAULT_HEARTBEAT_S = 2.0
_OKU = """Makine islem logu (hafif)
================================
Goruntu kaydi YOK. Satirlar islem sirasi + seyrek ornek (heartbeat).

Admin'de Log baslangic / Log bitis (TR saati) ile pencere acilir.
Baslangic bos ise bitis gelecekteyse hemen baslar; bitis gelince durur.

kind:
  state  - OPEN/CLOSED/MOVING/UNKNOWN degisimi
  cycle  - sayilan dongu (detail=cycle_s)
  lost   - sinyal kaybi
  tick   - ~2 sn sabit ornek (+ cpu/temp/load/mem)

Host sutunlari (tick'te dolu; diger kind'larda bos olabilir):
  cpu_pct  - islemci yuzde
  temp_c   - SoC sicaklik C
  load1    - 1 dk load average
  mem_pct  - RAM kullanim yuzde (MemAvailable bazli)

Excel'de ; ayiricili ac.
"""


@dataclass
class _Window:
    start: datetime | None
    end: datetime


@dataclass
class _Cap:
    last_state: str = ""
    last_tick_mono: float = 0.0
    csv_date: str = ""
    csv_fh: object | None = None
    lines_since_flush: int = 0
    last_flush_mono: float = 0.0


class ProcessLog:
    def __init__(
        self,
        logs_dir: Path | None = None,
        *,
        heartbeat_s: float = _DEFAULT_HEARTBEAT_S,
    ):
        self.root = Path(logs_dir or settings.logs_dir) / "diag"
        self.heartbeat_s = max(1.0, float(heartbeat_s))
        self._windows: dict[int, _Window] = {}
        self._by: dict[int, _Cap] = {}
        self._wrote_readme = False

    def sync_windows(self, mapping: dict[int, tuple[str | None, str | None]]) -> None:
        """mapping: machine_id -> (diag_from, diag_until)."""
        next_map: dict[int, _Window] = {}
        now = datetime.now(timezone.utc)
        for mid, (raw_from, raw_until) in mapping.items():
            end = parse_diag_dt(raw_until)
            if end is None or end <= now:
                # Closed / expired — drop open file for this machine.
                cap = self._by.get(int(mid))
                if cap is not None:
                    self._close_cap(cap)
                continue
            start = parse_diag_dt(raw_from)
            if start is not None and start >= end:
                continue
            next_map[int(mid)] = _Window(start=start, end=end)
        # Close caps that left the active set
        for mid in list(self._by.keys()):
            if mid not in next_map:
                self._close_cap(self._by[mid])
        self._windows = next_map

    def enabled_for(self, mid: int) -> bool:
        w = self._windows.get(int(mid))
        if w is None:
            return False
        now = datetime.now(timezone.utc)
        if now >= w.end:
            return False
        if w.start is not None and now < w.start:
            return False
        return True

    def window_status(self, mid: int) -> dict:
        w = self._windows.get(int(mid))
        now = datetime.now(timezone.utc)
        if w is None:
            return {"active": False, "pending": False}
        if now >= w.end:
            return {"active": False, "pending": False}
        if w.start is not None and now < w.start:
            return {
                "active": False,
                "pending": True,
                "starts_in_s": max(0.0, (w.start - now).total_seconds()),
            }
        return {
            "active": True,
            "pending": False,
            "ends_in_s": max(0.0, (w.end - now).total_seconds()),
        }

    def close(self) -> None:
        for cap in self._by.values():
            self._close_cap(cap)
        self._by.clear()

    def _close_cap(self, cap: _Cap) -> None:
        if cap.csv_fh is not None:
            try:
                cap.csv_fh.flush()
                cap.csv_fh.close()
            except Exception:
                pass
            cap.csv_fh = None

    def _ensure(self, mid: int) -> _Cap:
        cap = self._by.get(mid)
        if cap is None:
            cap = _Cap()
            self._by[mid] = cap
        day = to_istanbul(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        if cap.csv_fh is None or cap.csv_date != day:
            self._close_cap(cap)
            d = self.root / f"machine_{mid}" / day
            d.mkdir(parents=True, exist_ok=True)
            if not self._wrote_readme:
                try:
                    (self.root / "OKU.txt").write_text(_OKU, encoding="utf-8")
                    self._wrote_readme = True
                except OSError:
                    pass
            fp = d / "process.csv"
            new = not fp.exists()
            cap.csv_fh = fp.open("a", encoding="utf-8", newline="")
            cap.csv_date = day
            cap.lines_since_flush = 0
            cap.last_flush_mono = time.monotonic()
            if new:
                cap.csv_fh.write(CSV_HEADER + "\n")
        return cap

    def _maybe_flush(self, cap: _Cap) -> None:
        if cap.csv_fh is None:
            return
        now = time.monotonic()
        if cap.lines_since_flush >= _FLUSH_EVERY_LINES or (now - cap.last_flush_mono) >= _FLUSH_EVERY_S:
            try:
                cap.csv_fh.flush()
            except OSError:
                pass
            cap.lines_since_flush = 0
            cap.last_flush_mono = now

    def _write(
        self,
        mid: int,
        kind: str,
        state: str,
        pos: float | None,
        age_ms: float,
        detail: str = "",
        *,
        with_host: bool = False,
    ) -> None:
        if not self.enabled_for(mid):
            return
        cap = self._ensure(mid)
        now = datetime.now(timezone.utc)
        t_iso = to_istanbul(now).isoformat()
        mono = f"{time.monotonic():.3f}"
        pos_s = f"{pos:.4f}" if pos is not None else ""
        age_s = f"{age_ms:.0f}" if age_ms >= 0 else ""
        if with_host:
            host = sample_host().as_csv_fields()
        else:
            host = ";;;"
        line = f"{t_iso};{mono};{kind};{state};{pos_s};{age_s};{detail};{host}"
        try:
            assert cap.csv_fh is not None
            cap.csv_fh.write(line + "\n")
            cap.lines_since_flush += 1
            self._maybe_flush(cap)
        except Exception as e:
            logger.warning("process log write failed mid=%s: %s", mid, e)

    def on_frame(
        self,
        machine_id: int,
        *,
        state: str,
        pos: float | None,
        age_ms: float = -1.0,
        cycle_s: float | None = None,
        signal_lost: bool = False,
    ) -> None:
        mid = int(machine_id)
        if not self.enabled_for(mid):
            return
        cap = self._by.get(mid) or _Cap()
        if mid not in self._by:
            self._by[mid] = cap

        st = state or ""
        if signal_lost:
            self._write(mid, "lost", st, pos, age_ms)
            cap.last_state = st
            return

        if cycle_s is not None and cycle_s > 0:
            self._write(mid, "cycle", st, pos, age_ms, detail=f"{cycle_s:.3f}")

        if st and st != cap.last_state:
            self._write(mid, "state", st, pos, age_ms, detail=f"{cap.last_state}->{st}" if cap.last_state else st)
            cap.last_state = st

        now_m = time.monotonic()
        if (now_m - cap.last_tick_mono) >= self.heartbeat_s:
            self._write(mid, "tick", st, pos, age_ms, with_host=True)
            cap.last_tick_mono = now_m
