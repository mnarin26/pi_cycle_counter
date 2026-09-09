"""Anomaly window recorder: keep a pos ring; on SHORT/LONG dump ±N cycles to CSV.

Primary artifact is timestamped pos list (t_iso;pos) for zaman-pos charts.

Performance notes:
- Ticks are throttled (default ~10 Hz) so vision threads are not taxed every frame.
- Disk flush runs on a background writer thread (never blocks the vision loop).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.time_windows import to_istanbul

logger = logging.getLogger(__name__)

_OKU = """Anomali penceresi kayitlari
================================
SHORT/LONG dongu + onceki/sonraki turlarin zaman damgali pos listesi.

pos.csv  : t_iso;mono;pos;cycle_idx;role  (grafik: X=t_iso Y=pos)
meta.json: tetik, esikler, tur sureleri

Kamera goruntusu yok. Kayit sadece olay kapaninca (arka plan thread) yazilir.
"""

CSV_HEADER = "t_iso;mono;pos;cycle_idx;role"


def _parse_ids(raw: str | None) -> set[int] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    out: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out if out else None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


@dataclass
class _Tick:
    t_iso: str
    mono: float
    pos: float | None
    seq: int


@dataclass
class _CycleMark:
    t_end: str
    cycle_s: float
    seq_start: int
    seq_end: int
    kind: str
    role: str = "pre"


@dataclass
class _Armed:
    machine_id: int
    post_left: int
    trigger_cycle_s: float
    trigger_kind: str
    trigger_t_end: str
    started_mono: float
    cycles: list[_CycleMark] = field(default_factory=list)
    extra_triggers: list[dict[str, Any]] = field(default_factory=list)
    seq_start: int = 0


@dataclass
class _MachineState:
    ticks: deque = field(default_factory=lambda: deque(maxlen=6000))
    cycles: deque = field(default_factory=lambda: deque(maxlen=64))
    next_seq: int = 0
    cycle_seq_start: int = 0
    last_tick_mono: float = 0.0
    armed: _Armed | None = None


@dataclass
class _FlushJob:
    folder: Path
    csv_text: str
    meta_text: str


class AnomalyWindowRecorder:
    def __init__(self, logs_dir: Path | None = None) -> None:
        self.root = Path(logs_dir or settings.logs_dir) / "anomaly"
        self._lock = threading.Lock()
        self._by: dict[int, _MachineState] = {}
        self._wrote_readme = False
        self._flush_q: queue.SimpleQueue[_FlushJob | None] = queue.SimpleQueue()
        self.reload_settings()
        self._writer = threading.Thread(
            target=self._writer_loop, name="anomaly-writer", daemon=True
        )
        self._writer.start()
        logger.info(
            "anomaly window enabled=%s short=%.1fs long=%.1fs pre=%s post=%s "
            "tick_ms=%.0f ring=%s dir=%s",
            self.enabled,
            self.short_s,
            self.long_s,
            self.pre_cycles,
            self.post_cycles,
            self.tick_interval_s * 1000.0,
            self._ring_maxlen,
            self.root,
        )

    def reload_settings(self) -> None:
        self.enabled = bool(getattr(settings, "anomaly_enabled", True))
        self.short_s = float(getattr(settings, "anomaly_short_s", 10.0))
        self.long_s = float(getattr(settings, "anomaly_long_s", 40.0))
        self.pre_cycles = max(0, int(getattr(settings, "anomaly_pre_cycles", 5)))
        self.post_cycles = max(0, int(getattr(settings, "anomaly_post_cycles", 5)))
        self.max_cycles = max(8, int(getattr(settings, "anomaly_max_cycles", 40)))
        self.max_window_s = float(getattr(settings, "anomaly_max_window_s", 900.0))
        self.machine_ids = _parse_ids(getattr(settings, "anomaly_machine_ids", ""))
        self._ring_maxlen = max(500, int(getattr(settings, "anomaly_ring_ticks", 6000)))
        tick_ms = float(getattr(settings, "anomaly_tick_interval_ms", 100.0))
        self.tick_interval_s = max(0.02, tick_ms / 1000.0)

    def close(self) -> None:
        try:
            self._flush_q.put_nowait(None)
        except Exception:
            pass

    def _writer_loop(self) -> None:
        while True:
            job = self._flush_q.get()
            if job is None:
                return
            try:
                job.folder.mkdir(parents=True, exist_ok=True)
                (job.folder / "pos.csv").write_text(job.csv_text, encoding="utf-8")
                (job.folder / "meta.json").write_text(job.meta_text, encoding="utf-8")
                logger.info(
                    "anomaly disk-written path=%s bytes_csv=%s",
                    job.folder,
                    len(job.csv_text),
                )
            except Exception:
                logger.exception("anomaly writer failed path=%s", getattr(job, "folder", "?"))

    def _allowed(self, machine_id: int) -> bool:
        if not self.enabled:
            return False
        if self.machine_ids is None:
            return True
        return int(machine_id) in self.machine_ids

    def _state(self, mid: int) -> _MachineState:
        st = self._by.get(mid)
        if st is None:
            st = _MachineState()
            st.ticks = deque(maxlen=self._ring_maxlen)
            self._by[mid] = st
        return st

    def _classify(self, cycle_s: float) -> str | None:
        if cycle_s < self.short_s:
            return "short"
        if cycle_s >= self.long_s:
            return "long"
        return None

    def _ensure_readme(self) -> None:
        if self._wrote_readme:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        fp = self.root / "OKU.txt"
        if not fp.exists():
            fp.write_text(_OKU, encoding="utf-8")
        self._wrote_readme = True

    def on_tick(
        self,
        machine_id: int,
        *,
        t_iso: str | None = None,
        mono: float | None = None,
        pos: float | None = None,
    ) -> None:
        if not self._allowed(machine_id):
            return
        mid = int(machine_id)
        now = float(mono if mono is not None else time.monotonic())
        with self._lock:
            st = self._state(mid)
            if st.last_tick_mono > 0 and (now - st.last_tick_mono) < self.tick_interval_s:
                return
            st.last_tick_mono = now
            seq = st.next_seq
            st.next_seq += 1
            st.ticks.append(
                _Tick(
                    t_iso=t_iso or _iso_now(),
                    mono=now,
                    pos=None if pos is None else float(pos),
                    seq=seq,
                )
            )

    def on_cycle(self, machine_id: int, cycle_s: float, t_end: str | None = None) -> Path | None:
        """Queue a flush when window closes; returns folder path if queued."""
        if not self._allowed(machine_id):
            return None
        mid = int(machine_id)
        cs = float(cycle_s)
        if cs <= 0.05:
            return None
        t_end_iso = t_end or _iso_now()
        kind = self._classify(cs) or "normal"

        with self._lock:
            st = self._state(mid)
            seq_end = st.next_seq - 1
            if seq_end < 0:
                seq_end = 0
            mark = _CycleMark(
                t_end=t_end_iso,
                cycle_s=cs,
                seq_start=st.cycle_seq_start,
                seq_end=max(st.cycle_seq_start, seq_end),
                kind=kind,
            )
            st.cycle_seq_start = seq_end + 1
            st.cycles.append(mark)

            if st.armed is None:
                if kind in ("short", "long"):
                    self._arm(st, mid, mark)
                return None
            return self._on_armed_cycle(st, mid, mark)

    def _arm(self, st: _MachineState, mid: int, trigger: _CycleMark) -> None:
        pre = list(st.cycles)[:-1]
        pre = pre[-self.pre_cycles :] if self.pre_cycles else []
        for c in pre:
            c.role = "pre"
        trigger.role = "trigger"
        window_cycles = pre + [trigger]
        if window_cycles:
            seq_start = window_cycles[0].seq_start
        else:
            seq_start = max(0, trigger.seq_end - 50)

        st.armed = _Armed(
            machine_id=mid,
            post_left=self.post_cycles,
            trigger_cycle_s=trigger.cycle_s,
            trigger_kind=trigger.kind,
            trigger_t_end=trigger.t_end,
            started_mono=time.monotonic(),
            cycles=list(window_cycles),
            seq_start=seq_start,
        )
        logger.info(
            "anomaly armed mid=%s kind=%s cycle_s=%.2f pre=%s post_left=%s",
            mid,
            trigger.kind,
            trigger.cycle_s,
            len(pre),
            st.armed.post_left,
        )

    def _on_armed_cycle(self, st: _MachineState, mid: int, mark: _CycleMark) -> Path | None:
        armed = st.armed
        assert armed is not None

        if mark.kind in ("short", "long"):
            mark.role = "trigger"
            armed.extra_triggers.append(
                {
                    "t_end": mark.t_end,
                    "cycle_s": mark.cycle_s,
                    "kind": mark.kind,
                }
            )
            armed.post_left = self.post_cycles
            logger.info(
                "anomaly extend mid=%s kind=%s cycle_s=%.2f post_left=%s extras=%s",
                mid,
                mark.kind,
                mark.cycle_s,
                armed.post_left,
                len(armed.extra_triggers),
            )
        else:
            mark.role = "post"
            armed.post_left = max(0, armed.post_left - 1)

        armed.cycles.append(mark)

        force = False
        if len(armed.cycles) >= self.max_cycles:
            force = True
        if (time.monotonic() - armed.started_mono) >= self.max_window_s:
            force = True

        if armed.post_left <= 0 or force:
            path = self._queue_flush(st, armed, forced=force)
            st.armed = None
            return path
        return None

    def _queue_flush(self, st: _MachineState, armed: _Armed, *, forced: bool) -> Path:
        self._ensure_readme()
        ticks = [t for t in st.ticks if t.seq >= armed.seq_start]

        local = to_istanbul(datetime.now(timezone.utc))
        day = local.strftime("%Y-%m-%d")
        stamp = local.strftime("%H%M%S")
        folder = (
            self.root
            / f"machine_{armed.machine_id}"
            / day
            / f"{stamp}_s{armed.trigger_cycle_s:.2f}"
        )

        cycle_bounds: list[tuple[int, int, int, str]] = []
        for i, c in enumerate(armed.cycles):
            cycle_bounds.append((c.seq_start, c.seq_end, i, c.role))

        def role_for(seq: int) -> tuple[int, str]:
            for a, b, idx, role in cycle_bounds:
                if a <= seq <= b:
                    return idx, role
            for a, b, idx, role in reversed(cycle_bounds):
                if seq >= a:
                    return idx, role
            return 0, "pre"

        rows: list[str] = [CSV_HEADER]
        for t in ticks:
            idx, role = role_for(t.seq)
            rows.append(
                ";".join(
                    [
                        _csv_cell(t.t_iso),
                        _csv_cell(t.mono),
                        _csv_cell(t.pos),
                        _csv_cell(idx),
                        _csv_cell(role),
                    ]
                )
            )
        csv_text = "\n".join(rows) + "\n"
        meta = {
            "machine_id": armed.machine_id,
            "trigger_kind": armed.trigger_kind,
            "trigger_cycle_s": armed.trigger_cycle_s,
            "trigger_t_end": armed.trigger_t_end,
            "extra_triggers": armed.extra_triggers,
            "thresholds": {"short_s": self.short_s, "long_s": self.long_s},
            "pre_cycles": self.pre_cycles,
            "post_cycles": self.post_cycles,
            "forced_flush": forced,
            "cycles": [
                {
                    "t_end": c.t_end,
                    "cycle_s": c.cycle_s,
                    "kind": c.kind,
                    "role": c.role,
                    "seq_start": c.seq_start,
                    "seq_end": c.seq_end,
                }
                for c in armed.cycles
            ],
            "tick_count": len(ticks),
            "seq_start": armed.seq_start,
            "written_at": _iso_now(),
        }
        meta_text = json.dumps(meta, ensure_ascii=False, indent=2)
        self._flush_q.put(_FlushJob(folder=folder, csv_text=csv_text, meta_text=meta_text))
        logger.info(
            "anomaly flush-queued mid=%s path=%s ticks=%s cycles=%s forced=%s",
            armed.machine_id,
            folder,
            len(ticks),
            len(armed.cycles),
            forced,
        )
        return folder


def purge_anomaly_older_than(logs_dir: Path, days: int) -> int:
    root = Path(logs_dir) / "anomaly"
    if not root.is_dir() or days <= 0:
        return 0
    cutoff_day = datetime.now(timezone.utc).date() - timedelta(days=days)
    removed = 0
    for day_dir in root.glob("machine_*/20??-??-??"):
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= cutoff_day:
            continue
        import shutil

        shutil.rmtree(day_dir, ignore_errors=True)
        removed += 1
    return removed
