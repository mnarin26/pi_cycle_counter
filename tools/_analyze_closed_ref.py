#!/usr/bin/env python3
"""Analyze today's diag logs to suggest closed_ref and polarity."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TOOLS))

from replay_pos_ndjson import resolve_samples_file, PI_DIAG, LOCAL_DIAG  # noqa: E402

DIAG = PI_DIAG if PI_DIAG.exists() else LOCAL_DIAG
DB = Path("/home/pi/injection-monitor/backend/data/injection.db")
if not DB.exists():
    DB = BACKEND / "data" / "injection.db"
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"


def iter_pos(path: Path):
    if path.suffix.lower() == ".csv":
        import csv

        with path.open(encoding="utf-8", newline="") as fh:
            first = fh.readline()
            delim = ";" if ";" in first else ","
            fh.seek(0)
            for row in csv.DictReader(fh, delimiter=delim):
                if (row.get("k") or "").strip() == "stale_skip":
                    continue
                if not (row.get("mono") or "").strip():
                    continue
                p = (row.get("pos") or "").strip()
                if p:
                    yield float(p)
    else:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("k") not in (None, "", "f"):
                    continue
                p = rec.get("pos")
                if p is not None:
                    yield float(p)


def main() -> int:
    con = sqlite3.connect(str(DB))
    print(f"day={DAY} diag={DIAG}\n")
    for mid in (4, 5, 6):
        row = con.execute(
            "SELECT name, closed_position_1d, open_position_1d, axis_p0, axis_p1 "
            "FROM machines WHERE id=?",
            (mid,),
        ).fetchone()
        name = row[0] if row else f"AF-{mid}"
        db_closed = float(row[1]) if row and row[1] is not None else None
        db_open = float(row[2]) if row and row[2] is not None else None

        p = resolve_samples_file(DIAG, mid, DAY)
        nd = DIAG / f"machine_{mid}" / DAY / "samples.ndjson"
        if nd.exists():
            p = nd
        if p is None:
            print(f"#{mid} {name}: NO LOG for {DAY}")
            continue

        pos = list(iter_pos(p))
        if len(pos) < 100:
            print(f"#{mid} {name}: only {len(pos)} samples in {p.name}")
            continue

        s = sorted(pos)
        n = len(s)
        p05, p50, p95 = s[n // 20], s[n // 2], s[19 * n // 20]
        lo, hi = s[0], s[-1]
        # Histogram buckets to see where machine dwells
        buckets = Counter(int(round(x * 20)) for x in pos)  # 0.05 steps
        top = buckets.most_common(3)

        # Suggest: closed = extreme with more dwell near it
        low_dwell = sum(c for b, c in buckets.items() if b <= int(round(p05 * 20)) + 1)
        high_dwell = sum(c for b, c in buckets.items() if b >= int(round(p95 * 20)) - 1)
        if mid == 6:
            polarity = "high"
        else:
            polarity = "low" if low_dwell >= high_dwell else "high"
        closed_ref = p05 if polarity == "low" else p95

        print(f"#{mid} {name}  samples={n:,}  file={p.name}")
        print(f"  pos min={lo:.3f} p05={p05:.3f} med={p50:.3f} p95={p95:.3f} max={hi:.3f}")
        print(f"  DB closed={db_closed} open={db_open}")
        print(f"  dwell low-end={low_dwell} high-end={high_dwell} top_buckets={top}")
        print(f"  => polarity={polarity}  closed_ref={closed_ref:.3f}")
        print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
