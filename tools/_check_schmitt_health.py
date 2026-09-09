#!/usr/bin/env python3
"""Post-schmitt deploy health: cycles, frame age, 2x merges, live snap."""
from __future__ import annotations

import csv
import json
import sqlite3
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")
DB = Path("/home/pi/injection-monitor/backend/data/injection.db")
DIAG = Path("/home/pi/injection-monitor/backend/logs/diag")
DEPLOY = datetime(2026, 9, 2, 11, 24, tzinfo=TR).astimezone(timezone.utc)
DAY = "2026-09-02"


def parse_dt(raw):
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def hist(times):
    bands = [("<8", 0, 8), ("8-12", 8, 12), ("12-22", 12, 22), ("22-28", 22, 28),
             ("28-40", 28, 40), ("40-56", 40, 56), ("56+", 56, 1e9)]
    return [(l, sum(1 for t in times if a <= t < b)) for l, a, b in bands]


def csv_stats(mid: int) -> dict:
    fp = DIAG / f"machine_{mid}" / DAY / "samples.csv"
    if not fp.exists():
        return {"ok": False}
    n = 0
    ages = []
    stale = 0
    monos = []
    last_mono = None
    gaps = []
    with fp.open(encoding="utf-8", newline="") as fh:
        r = csv.DictReader(fh, delimiter=";")
        for row in r:
            k = (row.get("k") or "").strip()
            if k == "stale_skip":
                stale += 1
                continue
            mono = (row.get("mono") or "").strip()
            if not mono:
                continue
            n += 1
            m = float(mono)
            monos.append(m)
            if last_mono is not None:
                gaps.append(m - last_mono)
            last_mono = m
            age = (row.get("age_ms") or "").strip()
            if age:
                try:
                    ages.append(float(age))
                except ValueError:
                    pass
    if not monos:
        return {"ok": False, "n": 0, "stale": stale}
    gaps_s = sorted(g for g in gaps if g > 0)
    ages_s = sorted(ages) if ages else []
    span = monos[-1] - monos[0]
    hz = (n - 1) / span if span > 1 else 0
    big_gaps = sum(1 for g in gaps if g > 0.5)
    return {
        "ok": True,
        "n": n,
        "stale": stale,
        "hz": hz,
        "span_min": span / 60,
        "age_p50": ages_s[len(ages_s) // 2] if ages_s else None,
        "age_p95": ages_s[int(len(ages_s) * 0.95)] if ages_s else None,
        "age_max": ages_s[-1] if ages_s else None,
        "gap_p50": gaps_s[len(gaps_s) // 2] if gaps_s else None,
        "gap_p95": gaps_s[int(len(gaps_s) * 0.95)] if gaps_s else None,
        "gap_max": gaps_s[-1] if gaps_s else None,
        "gaps_gt_0.5s": big_gaps,
    }


def cycle_window(con, mid, t0, t1):
    rows = con.execute(
        "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=? "
        "AND COALESCE(is_counted,1)=1 AND t_end>=? AND t_end<? ORDER BY t_end",
        (mid, t0.isoformat(), t1.isoformat()),
    ).fetchall()
    times = [float(r[0]) for r in rows if r[0] is not None]
    return times


def main():
    now = datetime.now(timezone.utc)
    print(f"now={now.astimezone(TR).isoformat()}  deploy={DEPLOY.astimezone(TR).isoformat()}")
    print(f"window since deploy: {(now - DEPLOY).total_seconds()/60:.1f} min\n")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    # Pre-deploy same-length window today (morning) for comparison
    pre_end = DEPLOY
    pre_start = DEPLOY - (now - DEPLOY)

    print("=== CYCLE COUNTS: pre-deploy window vs since-deploy ===")
    for mid in (4, 5, 6):
        pre = cycle_window(con, mid, pre_start, pre_end)
        post = cycle_window(con, mid, DEPLOY, now)
        mins = (now - DEPLOY).total_seconds() / 60
        # expected rate from pre median
        med_pre = sorted(pre)[len(pre) // 2] if pre else 16.5
        exp = mins * 60 / med_pre if med_pre > 0 else 0
        print(f"AF-{mid}")
        print(f"  PRE  n={len(pre)} med={med_pre:.1f}s  hist={dict(hist(pre))}")
        if post:
            med_post = sorted(post)[len(post) // 2]
            print(f"  POST n={len(post)} med={med_post:.1f}s  hist={dict(hist(post))}")
        else:
            print(f"  POST n=0")
        print(f"  expected~{exp:.0f} from pre median | post/expected={(len(post)/exp if exp else 0):.2f}")
        print()

    print("=== TODAY CSV frame health ===")
    for mid in (4, 5, 6):
        s = csv_stats(mid)
        if not s.get("ok"):
            print(f"AF-{mid}: no usable samples")
            continue
        print(
            f"AF-{mid}: frames={s['n']:,} stale={s['stale']:,} ~{s['hz']:.1f} Hz "
            f"span={s['span_min']:.0f}min  age_ms p50={s['age_p50']} p95={s['age_p95']} max={s['age_max']}  "
            f"gap_s p50={s['gap_p50']:.3f} p95={s['gap_p95']:.3f} max={s['gap_max']:.2f}  gaps>0.5s={s['gaps_gt_0.5s']}"
        )

    print("\n=== LIVE SNAPSHOT ===")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=20) as resp:
            d = json.loads(resp.read())
        for m in d.get("machines", []):
            if m.get("id") not in (4, 5, 6):
                continue
            print(
                f"  m{m['id']} state={m.get('state')} pos={m.get('position_01')} "
                f"mode={m.get('counting_mode')} ref={m.get('closed_ref_live')} "
                f"last={m.get('cycle_time_last')} emits={m.get('dbg_cycle_emit_count')} "
                f"fps={m.get('fps')} process_ms={m.get('process_ms')} age? "
                f"found_prom={m.get('prominence')} seg={m.get('segment_len')}"
            )
        print(f"  cpu_proxy={d.get('cpu_proxy')}")
    except Exception as e:
        print("snapshot fail:", e)

    # Recent post cycles detail
    print("\n=== LAST 12 POST-DEPLOY CYCLES PER MACHINE ===")
    for mid in (4, 5, 6):
        rows = con.execute(
            "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=? "
            "AND COALESCE(is_counted,1)=1 AND t_end>=? ORDER BY t_end DESC LIMIT 12",
            (mid, DEPLOY.isoformat()),
        ).fetchall()
        print(f"AF-{mid}:")
        for cs, te in rows:
            print(f"  {parse_dt(te).astimezone(TR).strftime('%H:%M:%S')}  {float(cs):.2f}s")

    con.close()


if __name__ == "__main__":
    main()
