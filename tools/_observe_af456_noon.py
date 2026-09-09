#!/usr/bin/env python3
"""Noon analysis for AF-4/5/6 observation run (CSV diag + DB cycles).

Usage on Pi:
  python3 tools/_observe_af456_noon.py
  python3 tools/_observe_af456_noon.py --day 2026-09-02
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")
UTC = timezone.utc

ROOT = Path("/home/pi/injection-monitor")
BACKEND = ROOT / "backend"
LOCAL_ROOT = Path(__file__).resolve().parents[1]
if not ROOT.exists():
    ROOT = LOCAL_ROOT
    BACKEND = ROOT / "backend"

DB = BACKEND / "data" / "injection.db"
DIAG = BACKEND / "logs" / "diag"
sys.path.insert(0, str(BACKEND))


def parse_dt(raw) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def day_bounds_tr(day: str) -> tuple[datetime, datetime]:
    y, m, d = (int(x) for x in day.split("-"))
    start = datetime(y, m, d, tzinfo=TR).astimezone(UTC)
    return start, start + timedelta(days=1)


def band(t: float) -> str:
    if t < 14:
        return "<14"
    if t < 22:
        return "14-22"
    if t < 28:
        return "22-28"
    if t < 40:
        return "28-40(2x)"
    if t < 56:
        return "40-56(3x)"
    return "56+"


def load_csv_samples(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            k = (row.get("k") or "").strip()
            if k and k != "f":
                continue
            mono = (row.get("mono") or "").strip()
            if not mono:
                continue
            pos_raw = (row.get("pos") or "").strip()
            rows.append(
                {
                    "mono": float(mono),
                    "pos": None if not pos_raw else float(pos_raw),
                    "state": (row.get("state") or "").strip(),
                    "chg": (row.get("chg") or "").strip(),
                }
            )
    return rows


def csv_dt_gaps(rows: list[dict]) -> list[float]:
    if len(rows) < 2:
        return []
    monos = [r["mono"] for r in rows]
    return [monos[i] - monos[i - 1] for i in range(1, len(monos))]


def analyze_machine(con: sqlite3.Connection, mid: int, day: str, t0: datetime, t1: datetime) -> None:
    name = con.execute("SELECT name, min_prominence FROM machines WHERE id=?", (mid,)).fetchone()
    print(f"\n{'='*64}\nAF-{mid} {name[0]}  min_prominence={name[1]}")

    cycles = con.execute(
        """
        SELECT cycle_time_s, t_end FROM cycles
        WHERE machine_id=? AND t_end >= ? AND t_end < ?
        ORDER BY t_end
        """,
        (mid, t0.isoformat(), t1.isoformat()),
    ).fetchall()
    times = [float(r[0]) for r in cycles if r[0] is not None]
    print(f"DB cycles ({day}): {len(times)}")
    if times:
        s = sorted(times)
        bc = Counter(band(t) for t in times)
        print(
            f"  median={s[len(s)//2]:.2f}s  2x={bc.get('28-40(2x)',0)} "
            f"3x={bc.get('40-56(3x)',0)}  <14={bc.get('<14',0)}"
        )

    csv_path = DIAG / f"machine_{mid}" / day / "samples.csv"
    ndjson_path = DIAG / f"machine_{mid}" / day / "samples.ndjson"
    samples = load_csv_samples(csv_path)
    src = "samples.csv" if samples else "yok"
    if not samples and ndjson_path.exists():
        src = f"samples.ndjson ({ndjson_path.stat().st_size//1024} KB, eski format)"
    print(f"Diag: {src}  satir={len(samples)}")
    if samples:
        gaps = csv_dt_gaps(samples)
        if gaps:
            gs = sorted(gaps)
            med_gap = gs[len(gs) // 2]
            big = sum(1 for g in gaps if g > 0.15)
            print(f"  ornekleme: medyan aralik={med_gap*1000:.0f}ms  >150ms={big}/{len(gaps)}")
        chg = [r["chg"] for r in samples if r.get("chg")]
        if chg:
            print(f"  state gecisleri: {len(chg)}  ornek: {chg[:5]}")
        states = Counter(r["state"] for r in samples if r.get("state"))
        if states:
            print(f"  state dagilimi: {dict(states)}")

    if times and samples:
        try:
            import importlib.util

            replay_mod = ROOT / "tools" / "replay_pos_ndjson.py"
            spec = importlib.util.spec_from_file_location("replay_pos", replay_mod)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                path = mod.resolve_samples_file(DIAG, mid, day)
                if path:
                    prom = float(name[1] or 0.1)
                    mc = con.execute(
                        "SELECT min_change,debounce_ms,stability_confirm_ms FROM machines WHERE id=?",
                        (mid,),
                    ).fetchone()
                    r = mod.replay_file(
                        path,
                        min_change=float(mc[0] or 0.006),
                        min_prominence=prom,
                        debounce_ms=int(mc[1] or 80),
                        stability_ms=int(mc[2] or 500),
                    )
                    print(
                        f"  replay dongu: {len(r['emits'])}  DB dongu: {len(times)}  "
                        f"fark: {len(r['emits']) - len(times):+d}"
                    )
        except Exception as e:
            print(f"  replay atlandi: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="YYYY-MM-DD TR (default: bugun)")
    args = ap.parse_args()
    day = args.day or datetime.now(TR).strftime("%Y-%m-%d")
    t0, t1 = day_bounds_tr(day)

    print(f"AF-4/5/6 ogle analizi  gun={day} TR  DB={DB}")
    if not DB.exists():
        print("DB bulunamadi:", DB)
        return 1

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    for mid in (4, 5, 6):
        analyze_machine(con, mid, day, t0, t1)
    con.close()
    print("\nBitti.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
