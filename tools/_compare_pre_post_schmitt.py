#!/usr/bin/env python3
"""Deep compare pre vs post schmitt: rates, short/merged cycles, frame gaps."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")
DB = Path("/home/pi/injection-monitor/backend/data/injection.db")
DIAG = Path("/home/pi/injection-monitor/backend/logs/diag")
# Deploy ~11:24 TR
DEPLOY_UTC = "2026-09-02 08:24:00"
NOW_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def bands(times):
    return {
        "n": len(times),
        "med": sorted(times)[len(times) // 2] if times else 0,
        "lt10": sum(1 for t in times if t < 10),
        "10_14": sum(1 for t in times if 10 <= t < 14),
        "14_22": sum(1 for t in times if 14 <= t < 22),
        "22_28": sum(1 for t in times if 22 <= t < 28),
        "28_40": sum(1 for t in times if 28 <= t < 40),
        "40p": sum(1 for t in times if t >= 40),
    }


def fetch(con, mid, t0, t1):
    rows = con.execute(
        "SELECT cycle_time_s FROM cycles WHERE machine_id=? "
        "AND COALESCE(is_counted,1)=1 AND t_end>=? AND t_end<?",
        (mid, t0, t1),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def post_csv_gaps(mid: int, since_iso_utc: str):
    """Frame gaps after deploy from today's CSV (t_iso filter)."""
    fp = DIAG / f"machine_{mid}" / "2026-09-02" / "samples.csv"
    if not fp.exists():
        return None
    # since_iso like 2026-09-02T08:24:00
    since = since_iso_utc.replace(" ", "T")
    last = None
    gaps = []
    ages = []
    n = 0
    with fp.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            t = row.get("t_iso") or ""
            if t < since:
                continue
            if (row.get("k") or "").strip() == "stale_skip":
                continue
            mono = (row.get("mono") or "").strip()
            if not mono:
                continue
            n += 1
            m = float(mono)
            if last is not None:
                gaps.append(m - last)
            last = m
            a = (row.get("age_ms") or "").strip()
            if a:
                ages.append(float(a))
    if not gaps:
        return {"n": n}
    gs = sorted(g for g in gaps if g > 0)
    ages_s = sorted(ages) if ages else []
    span = sum(gs)
    return {
        "n": n,
        "hz": (n - 1) / span if span > 1 else 0,
        "gap_p50": gs[len(gs) // 2],
        "gap_p95": gs[int(len(gs) * 0.95)],
        "gap_max": gs[-1],
        "gaps_gt_0.3": sum(1 for g in gs if g > 0.3),
        "gaps_gt_0.5": sum(1 for g in gs if g > 0.5),
        "gaps_gt_1.0": sum(1 for g in gs if g > 1.0),
        "age_p50": ages_s[len(ages_s) // 2] if ages_s else None,
        "age_p95": ages_s[int(len(ages_s) * 0.95)] if ages_s else None,
        "age_max": ages_s[-1] if ages_s else None,
    }


def main():
    # Equal-length windows: post = deploy..now, pre = same duration before deploy
    now = datetime.now(timezone.utc)
    deploy = datetime.fromisoformat("2026-09-02T08:24:00+00:00")
    dur = now - deploy
    pre0 = (deploy - dur).strftime("%Y-%m-%d %H:%M:%S")
    pre1 = DEPLOY_UTC
    post0 = DEPLOY_UTC
    post1 = NOW_UTC
    print(f"PRE  [{pre0} .. {pre1}]  ({dur.total_seconds()/60:.1f} min)")
    print(f"POST [{post0} .. {post1}]\n")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for mid, name, typ_med in ((4, "AF-4", 16.5), (5, "AF-5", 17.0), (6, "AF-6", 26.0)):
        pre = fetch(con, mid, pre0, pre1)
        post = fetch(con, mid, post0, post1)
        bp, bo = bands(pre), bands(post)
        exp = dur.total_seconds() / typ_med
        print(f"{name}  typical~{typ_med}s  expected~{exp:.0f}")
        print(f"  PRE  {bp}")
        print(f"  POST {bo}")
        if bp["n"] and bo["n"]:
            print(
                f"  rate PRE={bp['n']/dur.total_seconds()*60:.1f}/min  "
                f"POST={bo['n']/dur.total_seconds()*60:.1f}/min  "
                f"count_ratio={bo['n']/bp['n']:.2f}"
            )
            print(
                f"  short(<10) PRE={bp['lt10']}({100*bp['lt10']/max(1,bp['n']):.0f}%) "
                f"POST={bo['lt10']}({100*bo['lt10']/max(1,bo['n']):.0f}%)  |  "
                f"2x(28-40) PRE={bp['28_40']} POST={bo['28_40']}"
            )
        print()

    print("=== POST-DEPLOY CSV frame gaps ===")
    for mid in (4, 5, 6):
        g = post_csv_gaps(mid, "2026-09-02T08:24:00")
        print(f"AF-{mid}: {g}")

    # Last 20 AF-4 cycles with flags
    print("\n=== AF-4 last 20 POST cycles (flag short/2x) ===")
    rows = con.execute(
        "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=4 "
        "AND t_end>=? ORDER BY t_end DESC LIMIT 20",
        (post0,),
    ).fetchall()
    for cs, te in rows:
        t = float(cs)
        flag = "SHORT" if t < 10 else ("2x?" if 28 <= t < 40 else ("3x?" if t >= 40 else "ok"))
        print(f"  {te}  {t:6.2f}s  {flag}")

    print("\n=== AF-6 last 15 POST ===")
    rows = con.execute(
        "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=6 "
        "AND t_end>=? ORDER BY t_end DESC LIMIT 15",
        (post0,),
    ).fetchall()
    for cs, te in rows:
        t = float(cs)
        flag = "SHORT" if t < 14 else ("2x?" if 40 <= t < 56 else ("ok" if 20 <= t < 34 else "odd"))
        print(f"  {te}  {t:6.2f}s  {flag}")

    con.close()


if __name__ == "__main__":
    main()
