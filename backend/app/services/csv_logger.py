"""Append-only daily CSV per machine."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

_locks: dict[str, Lock] = {}


def _lock_for(path: Path) -> Lock:
    key = str(path.resolve())
    if key not in _locks:
        _locks[key] = Lock()
    return _locks[key]


def append_cycle_row(
    logs_dir: Path,
    machine_id: int,
    machine_name: str,
    cycle_time_s: float,
    state: str,
    mold_name: str | None,
    confidence: float,
) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = logs_dir / f"machine_{machine_id}"
    root.mkdir(parents=True, exist_ok=True)
    fp = root / f"{day}.csv"
    lk = _lock_for(fp)
    header = ["Timestamp", "Machine", "CycleTime", "State", "MoldName", "Confidence"]
    with lk:
        new_file = not fp.exists()
        with fp.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(header)
            w.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    machine_name,
                    f"{cycle_time_s:.4f}",
                    state,
                    mold_name or "",
                    f"{confidence:.4f}",
                ]
            )


def append_state_row(
    logs_dir: Path,
    machine_id: int,
    machine_name: str,
    state: str,
    mold_name: str | None,
    confidence: float,
) -> None:
    append_cycle_row(logs_dir, machine_id, machine_name, 0.0, state, mold_name, confidence)
