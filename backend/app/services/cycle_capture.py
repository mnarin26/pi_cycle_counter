"""Diagnostic capture: per-frame CSV log for offline replay (no images).

Writes the same timeline the line probe counted — position, state, timestamps.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.services.time_windows import to_istanbul

logger = logging.getLogger(__name__)

_OKU = """Makine teshis kayitlari
================================
Bu klasor, donguyu SAYDIGIMIZ zaman cizelgesidir (kamera goruntusu yok).

Kayit, admin paneldeki makine ayarinda "Log bitis" tarihine kadar acilir;
sure dolunca otomatik durur.

samples.csv : her islenen yeni kare (zaman, state, pos, found, occlusion, kare yasi)
              Excel'de acmak icin noktali virgul (;) ayiricilidir.

Tahmini boyut: ~15-20 MB / gun / makine.
Eski gunlerde samples.ndjson + clips/ klasorleri kalabilir (geriye uyum).
"""

# Empirical from AF-1/4/5 full-day logs (~17-19 MiB/day).
SAMPLES_MB_PER_DAY = 18.0

CSV_HEADER = (
    "t_iso;mono;age_ms;state;pos;found;occ;prom;seg;cycle_age_s;chg;k"
)

_FLUSH_EVERY_LINES = 50
_FLUSH_EVERY_S = 1.0


def parse_diag_until(raw: str | None) -> datetime | None:
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


def machine_diag_dir(logs_dir: Path | None, machine_id: int) -> Path:
    root = Path(logs_dir or settings.logs_dir) / "diag" / f"machine_{int(machine_id)}"
    return root


def dir_size_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def estimate_samples_bytes(until: datetime | None, *, now: datetime | None = None) -> int:
    """Rough samples.csv growth from now until `until`."""
    if until is None:
        return 0
    now = now or datetime.now(timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    secs = max(0.0, (until - now).total_seconds())
    return int(secs / 86400.0 * SAMPLES_MB_PER_DAY * 1024 * 1024)


def format_bytes(n: int) -> str:
    x = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(x)} {unit}"
            return f"{x:.1f} {unit}"
        x /= 1024.0
    return f"{n} B"


def parse_diag_ids(raw: str | None) -> set[int]:
    out: set[int] = set()
    for part in str(raw or "").split(",") :
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


@dataclass
class _MachineCap:
    last_frame_mono: float = 0.0
    last_state: str = ""
    csv_date: str = ""
    csv_fp: Path | None = None
    csv_fh: object | None = None
    lines_since_flush: int = 0
    last_flush_mono: float = 0.0
    last_stale_log_mono: float = 0.0
    last_write_mono: float = 0.0
    recent: list[float] = field(default_factory=list)


class CycleCapture:
    def __init__(self, logs_dir: Path | None = None):
        self.ids = parse_diag_ids(settings.diag_machine_ids)
        # Machines that log continuously regardless of diag_until (e.g. schmitt
        # mode: keep the full pos timeline so a mis-count can be replayed).
        self.always_on: set[int] = set()
        self._until: dict[int, datetime] = {}
        self._sample_interval_s: float = 0.1
        self.root = Path(logs_dir or settings.logs_dir) / "diag"
        self._by: dict[int, _MachineCap] = {}
        self._wrote_readme = False
        logger.info(
            "diag capture legacy_ids=%s dir=%s (timed via machines.diag_until, CSV only)",
            sorted(self.ids),
            self.root,
        )

    def sync_until_map(self, mapping: dict[int, str | None]) -> None:
        next_map: dict[int, datetime] = {}
        now = datetime.now(timezone.utc)
        for mid, raw in mapping.items():
            until = parse_diag_until(raw)
            if until is not None and until > now:
                next_map[int(mid)] = until
        self._until = next_map

    def set_sample_interval_ms(self, ms: int) -> None:
        self._sample_interval_s = max(0.05, float(ms) / 1000.0)

    def set_always_on(self, ids: set[int]) -> None:
        """Machines that always record (continuous log). When a machine leaves
        the set and has no active diag_until, its file is flushed/closed."""
        ids = {int(x) for x in ids}
        dropped = self.always_on - ids
        self.always_on = ids
        for mid in dropped:
            if mid not in self._until and mid not in self.ids:
                cap = self._by.get(mid)
                if cap is not None:
                    self._close_csv(cap)

    def enabled_for(self, machine_id: int) -> bool:
        mid = int(machine_id)
        if mid in self.always_on:
            return True
        until = self._until.get(mid)
        if until is not None:
            if datetime.now(timezone.utc) < until:
                return True
            self._until.pop(mid, None)
            cap = self._by.get(mid)
            if cap is not None:
                self._close_csv(cap)
            return False
        return mid in self.ids

    def close(self) -> None:
        for cap in self._by.values():
            self._close_csv(cap)

    def _cap(self, mid: int) -> _MachineCap:
        cap = self._by.get(mid)
        if cap is None:
            cap = _MachineCap()
            self._by[mid] = cap
        return cap

    def _day_dir(self, mid: int, local_date: str) -> Path:
        return self.root / f"machine_{mid}" / local_date

    def _ensure_readme(self) -> None:
        if self._wrote_readme:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        fp = self.root / "OKU.txt"
        fp.write_text(_OKU, encoding="utf-8")
        self._wrote_readme = True

    def _close_csv(self, cap: _MachineCap) -> None:
        fh = cap.csv_fh
        cap.csv_fh = None
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        cap.lines_since_flush = 0

    def _ensure_csv(self, mid: int, cap: _MachineCap) -> None:
        self._ensure_readme()
        day = to_istanbul(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        if cap.csv_date == day and cap.csv_fh is not None:
            return
        self._close_csv(cap)
        d = self._day_dir(mid, day)
        d.mkdir(parents=True, exist_ok=True)
        fp = d / "samples.csv"
        new_file = not fp.exists()
        cap.csv_fh = fp.open("a", encoding="utf-8", newline="")
        cap.csv_fp = fp
        cap.csv_date = day
        cap.last_flush_mono = time.monotonic()
        if new_file:
            cap.csv_fh.write(CSV_HEADER + "\n")

    def _maybe_flush(self, cap: _MachineCap) -> None:
        now = time.monotonic()
        if cap.csv_fh is None:
            return
        if cap.lines_since_flush >= _FLUSH_EVERY_LINES or (
            now - cap.last_flush_mono
        ) >= _FLUSH_EVERY_S:
            try:
                cap.csv_fh.flush()
            except Exception:
                pass
            cap.lines_since_flush = 0
            cap.last_flush_mono = now

    def _write_row(self, mid: int, cap: _MachineCap, row: dict) -> None:
        self._ensure_csv(mid, cap)
        line = ";".join(
            _csv_cell(row.get(col))
            for col in (
                "t_iso",
                "mono",
                "age_ms",
                "state",
                "pos",
                "found",
                "occ",
                "prom",
                "seg",
                "cycle_age_s",
                "chg",
                "k",
            )
        )
        try:
            cap.csv_fh.write(line + "\n")
            cap.lines_since_flush += 1
            self._maybe_flush(cap)
        except Exception as e:
            logger.warning("diag csv write failed mid=%s: %s", mid, e)

    def expected_s(self, mid: int, mold_target: float | None, mold_avg: float | None) -> float:
        cap = self._cap(mid)
        cands: list[float] = []
        for v in (mold_target, mold_avg):
            if v is not None and float(v) >= 8.0:
                cands.append(float(v))
        normals = [x for x in cap.recent if x >= 8.0]
        if len(normals) >= 8:
            srt = sorted(normals)
            med = srt[len(srt) // 2]
            tight = [x for x in srt if 0.75 * med <= x <= 1.25 * med]
            if len(tight) >= 5:
                t2 = sorted(tight)
                cands.append(t2[len(t2) // 2])
        if not cands:
            return 16.5
        return min(cands)

    def note_cycle_time(self, mid: int, cycle_s: float, expected: float) -> None:
        if not self.enabled_for(mid):
            return
        cap = self._cap(mid)
        if float(cycle_s) < expected * 1.35:
            cap.recent.append(float(cycle_s))
            if len(cap.recent) > 40:
                cap.recent = cap.recent[-40:]
        elif len(cap.recent) < 8:
            cap.recent.append(float(cycle_s))
            if len(cap.recent) > 40:
                cap.recent = cap.recent[-40:]

    def log_stale(self, mid: int, frame_age_ms: float, state: str | None) -> None:
        if not self.enabled_for(mid):
            return
        cap = self._cap(mid)
        now = time.monotonic()
        if now - cap.last_stale_log_mono < 2.0:
            return
        cap.last_stale_log_mono = now
        self._write_row(
            mid,
            cap,
            {
                "t_iso": _iso_now(),
                "mono": "",
                "age_ms": round(float(frame_age_ms), 0),
                "state": state or "",
                "pos": "",
                "found": "",
                "occ": "",
                "prom": "",
                "seg": "",
                "cycle_age_s": "",
                "chg": "",
                "k": "stale_skip",
            },
        )

    def on_sample(
        self,
        *,
        machine_id: int,
        frame_mono: float,
        frame_age_ms: float,
        state: str,
        pos: float | None,
        found: bool,
        occlusion: bool,
        prominence: int,
        segment_len: int,
        cycle_age_s: float | None,
        extra: dict | None = None,
    ) -> None:
        if not self.enabled_for(machine_id):
            return
        cap = self._cap(machine_id)
        if frame_mono > 0 and (frame_mono - cap.last_write_mono) < self._sample_interval_s * 0.9:
            return
        if frame_mono <= 0 or abs(frame_mono - cap.last_frame_mono) < 1e-4:
            return
        cap.last_frame_mono = frame_mono
        cap.last_write_mono = frame_mono if frame_mono > 0 else time.monotonic()
        chg = ""
        if state != cap.last_state:
            chg = f"{cap.last_state}->{state}"
            cap.last_state = state
        row = {
            "t_iso": _iso_now(),
            "mono": round(frame_mono, 3),
            "age_ms": round(float(frame_age_ms), 0),
            "state": state,
            "pos": None if pos is None else round(float(pos), 4),
            "found": found,
            "occ": occlusion,
            "prom": int(prominence),
            "seg": int(segment_len),
            "cycle_age_s": None if cycle_age_s is None else round(float(cycle_age_s), 2),
            "chg": chg,
            "k": "",
        }
        if extra:
            for k, v in extra.items():
                if k in row:
                    row[k] = v
        self._write_row(machine_id, cap, row)


def purge_diag_older_than(logs_dir: Path, days: int) -> int:
    root = Path(logs_dir) / "diag"
    if not root.is_dir() or days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    for day_dir in root.glob("machine_*/20??-??-??"):
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        import shutil

        shutil.rmtree(day_dir, ignore_errors=True)
        removed += 1
    return removed
