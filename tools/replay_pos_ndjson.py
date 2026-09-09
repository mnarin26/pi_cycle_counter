#!/usr/bin/env python3
"""Offline AF-4/AF-5 count replay from diag samples.ndjson.

Reads stored per-frame `pos` and original `mono` intervals. Does not talk to
cameras, does not write SQLite, does not touch the live uvicorn process.

CycleTracker uses time.monotonic internally; that clock is patched to the
recorded frame timeline so a full day finishes in seconds without shrinking
debounce/stability.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.cycle_tracker import CycleTracker
from app.vision.schmitt_counter import SchmittConfig, SchmittCounter
from app.vision.state_machine import ClampStateMachine, ConfirmedZone, StateMachineConfig

TR = ZoneInfo("Europe/Istanbul")
UTC = timezone.utc

PI_DIAG = Path("/home/pi/injection-monitor/backend/logs/diag")
PI_DB = Path("/home/pi/injection-monitor/backend/data/injection.db")
LOCAL_DIAG = BACKEND / "logs" / "diag"
LOCAL_DB = BACKEND / "data" / "injection.db"


def _day_bounds_utc(day: str) -> tuple[datetime, datetime]:
    y, m, d = (int(x) for x in day.split("-"))
    start = datetime(y, m, d, tzinfo=TR).astimezone(UTC)
    return start, start + timedelta(days=1)


def _hist(times: list[float]) -> list[tuple[str, int]]:
    bands = [
        ("<8", 0.0, 8.0),
        ("8-12", 8.0, 12.0),
        ("12-22", 12.0, 22.0),
        ("22-28", 22.0, 28.0),
        ("28-40", 28.0, 40.0),
        ("40-56", 40.0, 56.0),
        ("56+", 56.0, 1e9),
    ]
    out = []
    for label, a, b in bands:
        out.append((label, sum(1 for t in times if a <= t < b)))
    return out


def _stats(times: list[float]) -> str:
    if not times:
        return "n=0"
    s = sorted(times)
    n = len(s)
    med = s[n // 2]
    mean = sum(s) / n
    main = [t for t in s if 12.0 <= t < 22.0]
    x2 = [t for t in s if 28.0 <= t < 40.0]
    x3 = [t for t in s if 40.0 <= t < 56.0]
    return (
        f"n={n} median={med:.2f}s mean={mean:.2f}s "
        f"min={s[0]:.2f} max={s[-1]:.2f} | "
        f"12-22={len(main)} 28-40(2x)={len(x2)} 40-56(3x)={len(x3)}"
    )


def _print_hist(title: str, times: list[float]) -> None:
    print(f"  {title}: {_stats(times)}")
    for label, c in _hist(times):
        bar = "#" * min(60, c // max(1, (len(times) // 40) or 1)) if c else ""
        print(f"    {label:7} {c:5d}  {bar}")


def _load_machine(db_path: Path, mid: int) -> dict:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(machines)")}
        fields = ["id", "name", "debounce_ms", "stability_confirm_ms", "occlusion_grace_ms"]
        if "min_change" in cols:
            fields.append("min_change")
        row = con.execute(
            f"SELECT {', '.join(fields)} FROM machines WHERE id=?", (mid,)
        ).fetchone()
        if not row:
            raise SystemExit(f"machine {mid} not in DB")
        data = dict(zip(fields, row))
        if "min_change" not in data or data.get("min_change") is None:
            data["min_change"] = 0.008
        return data
    finally:
        con.close()


def _load_db_cycles(db_path: Path, mid: int, t0: datetime, t1: datetime) -> list[float]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(cycles)")}
        counted = "AND COALESCE(is_counted, 1)=1" if "is_counted" in cols else ""
        rows = con.execute(
            f"SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=? {counted}",
            (mid,),
        ).fetchall()
    finally:
        con.close()
    out: list[float] = []
    for cs, tend in rows:
        if cs is None:
            continue
        if isinstance(tend, str):
            raw = tend.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                continue
        elif isinstance(tend, datetime):
            dt = tend
        else:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
        if t0 <= dt < t1:
            out.append(float(cs))
    return out


def _iter_ndjson_records(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("k") != "f":
                continue
            yield rec


def _iter_csv_records(path: Path):
    with path.open("r", encoding="utf-8", newline="") as fh:
        first = fh.readline()
        if not first:
            return
        delim = ";" if ";" in first else ","
        fh.seek(0)
        import csv as csv_mod

        reader = csv_mod.DictReader(fh, delimiter=delim)
        for row in reader:
            k = (row.get("k") or "").strip()
            if k and k != "f":
                continue
            if k == "stale_skip":
                continue
            mono_raw = (row.get("mono") or "").strip()
            if not mono_raw:
                continue
            try:
                mono = float(mono_raw)
            except ValueError:
                continue
            pos_raw = (row.get("pos") or "").strip()
            pos = None if not pos_raw else float(pos_raw)
            yield {"mono": mono, "pos": pos}


def resolve_samples_file(diag: Path, mid: int, day: str) -> Path | None:
    day_dir = diag / f"machine_{mid}" / day
    csv_path = day_dir / "samples.csv"
    if csv_path.exists():
        return csv_path
    ndjson_path = day_dir / "samples.ndjson"
    if ndjson_path.exists():
        return ndjson_path
    return None


def _iter_samples(path: Path):
    if path.suffix.lower() == ".csv":
        yield from _iter_csv_records(path)
    else:
        yield from _iter_ndjson_records(path)


def replay_file(
    samples_path: Path,
    *,
    min_change: float,
    min_prominence: float,
    debounce_ms: int,
    stability_ms: int,
) -> dict:
    sm = ClampStateMachine(
        cfg=StateMachineConfig(
            min_change=min_change,
            min_prominence=min_prominence,
            debounce_ms=debounce_ms,
            stability_confirm_ms=stability_ms,
        )
    )
    ct = CycleTracker()
    stability_s = max(0.5, float(stability_ms or 500) / 1000.0)
    ct.unknown_grace_s = max(3.0, stability_s * 6.0)
    ct.unknown_grace_after_extreme_s = 12.0

    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    n_frames = 0
    n_resets = 0
    emits: list[float] = []
    last_mono: float | None = None

    with patch("app.services.cycle_tracker.time.monotonic", mono):
        for rec in _iter_samples(samples_path):
            n_frames += 1
            m = rec.get("mono")
            if m is None:
                continue
            m = float(m)
            if last_mono is not None and m + 0.05 < last_mono:
                sm.reset()
                ct._reset_cycle()
                n_resets += 1
            last_mono = m
            clock["t"] = m
            pos = rec.get("pos")
            now_ms = m * 1000.0
            if pos is None:
                sm.reset()
                z = sm.step(None, now_ms)
            else:
                z = sm.step(float(pos), now_ms)
            dt = ct.on_confirmed(z, None if pos is None else float(pos))
            if dt is not None and dt > 0.05:
                emits.append(float(dt))

    return {
        "frames": n_frames,
        "resets": n_resets,
        "emits": emits,
    }


def replay_schmitt_file(
    samples_path: Path,
    *,
    polarity: str,
    closed_ref: float | None,
    closed_hyst: float,
    smooth_win: int,
    learn_enabled: bool,
) -> dict:
    """Run the recorded pos series through the new Schmitt counter.

    Uses the recorded `mono` timeline directly as the counter's clock, so
    learning cooldowns / dwell timers match real time. Fully deterministic.
    """
    sc = SchmittCounter(
        SchmittConfig(
            closed_polarity=polarity,
            closed_ref=closed_ref,
            closed_hyst=closed_hyst,
            smooth_win=smooth_win,
            learn_enabled=learn_enabled,
        )
    )
    n_frames = 0
    n_resets = 0
    emits: list[float] = []
    last_mono: float | None = None

    for rec in _iter_samples(samples_path):
        n_frames += 1
        m = rec.get("mono")
        if m is None:
            continue
        m = float(m)
        # A large backwards jump in mono means the process restarted; drop the
        # in-progress cycle timing but keep the learned closed_ref.
        if last_mono is not None and m + 0.05 < last_mono:
            sc.reset()
            n_resets += 1
        last_mono = m
        pos = rec.get("pos")
        dt = sc.step(None if pos is None else float(pos), m)
        if dt is not None and dt > 0.05:
            emits.append(float(dt))

    return {
        "frames": n_frames,
        "resets": n_resets,
        "emits": emits,
        "count": sc.count,
        "final_closed_ref": sc.closed_ref,
        "learn_events": list(sc.learn_events),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline pos replay — read-only, no live side effects")
    ap.add_argument("--day", default="2026-08-24")
    ap.add_argument("--ids", default="4,5")
    ap.add_argument(
        "--min-change",
        action="append",
        type=float,
        default=None,
        help="Extra MIN_CHANGE values besides the DB value (repeatable). Default: 0.008 0.012",
    )
    ap.add_argument(
        "--prom",
        action="append",
        type=float,
        default=None,
        help="Peak/trough swing amplitude to count a stroke end (repeatable). "
        "Default sweep: 0.05 0.08 0.10 0.12",
    )
    ap.add_argument(
        "--schmitt",
        action="store_true",
        help="Run the new single-edge Schmitt counter (with auto-learning) instead of peak/trough.",
    )
    ap.add_argument("--polarity", default="low", choices=["low", "high"],
                    help="Schmitt: closed end is at low (default) or high position.")
    ap.add_argument("--closed-ref", type=float, default=None,
                    help="Schmitt: fixed closed reference; omit to learn on warmup.")
    ap.add_argument("--hyst", type=float, default=0.05, help="Schmitt hysteresis half-width.")
    ap.add_argument("--smooth", type=int, default=5, help="Schmitt median smoothing window.")
    ap.add_argument("--no-learn", action="store_true", help="Schmitt: disable auto-learning.")
    args = ap.parse_args()
    extra = args.min_change if args.min_change else [0.006]
    proms = args.prom if args.prom else [0.05, 0.08, 0.10, 0.12]
    ids = [int(x) for x in args.ids.split(",") if x.strip()]

    diag = PI_DIAG if PI_DIAG.exists() else LOCAL_DIAG
    db_path = PI_DB if PI_DB.exists() else LOCAL_DB
    if not db_path.exists():
        print("DB not found", db_path)
        return 1

    t0, t1 = _day_bounds_utc(args.day)
    print(f"READ-ONLY replay day={args.day} TR  diag={diag}  db={db_path}")
    print("Does not write DB / restart services / touch cameras.\n")

    t_wall0 = time.perf_counter()
    for mid in ids:
        samples_path = resolve_samples_file(diag, mid, args.day)
        m = _load_machine(db_path, mid)
        db_times = _load_db_cycles(db_path, mid, t0, t1)
        print("=" * 72)
        print(
            f"#{mid} {m.get('name')}  debounce={m.get('debounce_ms')}ms  "
            f"stab={m.get('stability_confirm_ms')}ms  db_min_change={m.get('min_change')}"
        )
        if samples_path is None:
            print(f"  MISSING samples.csv/ndjson for machine_{mid}/{args.day}")
            _print_hist("DB cycles that day", db_times)
            continue
        size_mb = samples_path.stat().st_size / (1024 * 1024)
        print(f"  {samples_path.name} {samples_path}  ({size_mb:.1f} MiB)")
        _print_hist("DB (old live count)", db_times)

        if args.schmitt:
            r = replay_schmitt_file(
                samples_path,
                polarity=args.polarity,
                closed_ref=args.closed_ref,
                closed_hyst=args.hyst,
                smooth_win=args.smooth,
                learn_enabled=not args.no_learn,
            )
            ref = r["final_closed_ref"]
            ref_txt = "none" if ref is None else f"{ref:.3f}"
            tag = (
                f"SCHMITT polarity={args.polarity} hyst={args.hyst:.3f} smooth={args.smooth} "
                f"learn={not args.no_learn} frames={r['frames']} resets={r['resets']} "
                f"count={r['count']} final_closed_ref={ref_txt}"
            )
            _print_hist(tag, r["emits"])
            for ev in r["learn_events"]:
                old_txt = "none" if ev.old_ref is None else f"{ev.old_ref:.3f}"
                new_txt = "none" if ev.new_ref is None else f"{ev.new_ref:.3f}"
                print(f"    learn[{ev.kind}] {old_txt}->{new_txt} @t={ev.now_s:.1f} {ev.detail}")
            if db_times:
                print(f"    vs DB: count {r['count']}/{len(db_times)} ({r['count'] - len(db_times):+d})")
            print()
            continue

        variants = []
        db_mc = float(m.get("min_change") or 0.008)
        variants.append(db_mc)
        for x in extra:
            if abs(x - db_mc) > 1e-9:
                variants.append(float(x))

        for mc in variants:
            for pr in proms:
                r = replay_file(
                    samples_path,
                    min_change=mc,
                    min_prominence=pr,
                    debounce_ms=int(m.get("debounce_ms") or 80),
                    stability_ms=int(m.get("stability_confirm_ms") or 500),
                )
                tag = (
                    f"REPLAY min_change={mc:.4f} prom={pr:.3f} "
                    f"frames={r['frames']} mono_resets={r['resets']}"
                )
                _print_hist(tag, r["emits"])
                if db_times and r["emits"]:
                    db_x2 = sum(1 for t in db_times if 28.0 <= t < 40.0)
                    rp_x2 = sum(1 for t in r["emits"] if 28.0 <= t < 40.0)
                    db_n = len(db_times)
                    rp_n = len(r["emits"])
                    print(
                        f"    vs DB: count {rp_n}/{db_n} ({rp_n - db_n:+d})  "
                        f"2x-band {rp_x2}/{db_x2} ({rp_x2 - db_x2:+d})"
                    )
        print()

    print(f"wall {time.perf_counter() - t_wall0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
