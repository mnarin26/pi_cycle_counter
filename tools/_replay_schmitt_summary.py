#!/usr/bin/env python3
"""Quick Schmitt vs DB summary on Pi (read-only)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from replay_pos_ndjson import (  # noqa: E402
    LOCAL_DB,
    LOCAL_DIAG,
    PI_DB,
    PI_DIAG,
    _load_db_cycles,
    replay_file,
    replay_schmitt_file,
    resolve_samples_file,
)

TR = ZoneInfo("Europe/Istanbul")


def day_bounds(day: str):
    y, m, d = (int(x) for x in day.split("-"))
    s = datetime(y, m, d, tzinfo=TR).astimezone(timezone.utc)
    return s, s + timedelta(days=1)


def pick_samples(diag: Path, mid: int, day: str) -> Path | None:
    nd = diag / f"machine_{mid}" / day / "samples.ndjson"
    if nd.exists():
        return nd
    return resolve_samples_file(diag, mid, day)


def main() -> int:
    diag = PI_DIAG if PI_DIAG.exists() else LOCAL_DIAG
    db_path = PI_DB if PI_DB.exists() else LOCAL_DB
    cases = [
        ("2026-08-24", [4, 5]),
        ("2026-08-26", [4, 5]),
        ("2026-08-31", [6]),
        ("2026-09-01", [4, 5, 6]),
    ]
    print(f"diag={diag}  db={db_path}\n")
    for day, ids in cases:
        t0, t1 = day_bounds(day)
        for mid in ids:
            p = pick_samples(diag, mid, day)
            if p is None:
                print(f"AF-{mid} {day}: MISSING")
                continue
            db = _load_db_cycles(db_path, mid, t0, t1)
            sch = replay_schmitt_file(
                p,
                polarity="low",
                closed_ref=None,
                closed_hyst=0.05,
                smooth_win=5,
                learn_enabled=True,
            )
            peak = replay_file(
                p,
                min_change=0.006,
                min_prominence=0.10,
                debounce_ms=80,
                stability_ms=500,
            )
            db_x2 = sum(1 for t in db if 28 <= t < 40)
            sch_x2 = sum(1 for t in sch["emits"] if 28 <= t < 40)
            pk_x2 = sum(1 for t in peak["emits"] if 28 <= t < 40)
            print(
                f"AF-{mid} {day}  frames={sch['frames']:,}  "
                f"DB={len(db)}  peak={len(peak['emits'])}  schmitt={sch['count']}  "
                f"ref={sch['final_closed_ref']}"
            )
            print(
                f"  28-40s(2x): DB={db_x2} peak={pk_x2} schmitt={sch_x2}  "
                f"delta schmitt-DB={sch['count'] - len(db):+d}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
