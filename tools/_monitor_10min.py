#!/usr/bin/env python3
"""10-min monitor: snapshot latency, frame age, vision metrics, CSV gaps."""
from __future__ import annotations

import csv
import json
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")
DURATION_S = 600  # 10 minutes
POLL_S = 0.5
DIAG = Path("/home/pi/injection-monitor/backend/logs/diag")
DAY = datetime.now(TR).strftime("%Y-%m-%d")


def fetch_snapshot(cam_id: int) -> dict:
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:8000/api/cameras/{cam_id}/snapshot.jpg?t={time.time()}"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            age_hdr = resp.headers.get("X-Frame-Age-Ms", "")
            latency_ms = (time.monotonic() - t0) * 1000.0
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "size": len(data),
                "frame_age_ms": float(age_hdr) if age_hdr else None,
            }
    except Exception as e:
        return {"ok": False, "latency_ms": (time.monotonic() - t0) * 1000.0, "err": str(e)}


def fetch_live() -> dict:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=15) as r:
            d = json.loads(r.read())
            return {"ok": True, "latency_ms": (time.monotonic() - t0) * 1000.0, "data": d}
    except Exception as e:
        return {"ok": False, "latency_ms": (time.monotonic() - t0) * 1000.0, "err": str(e)}


def csv_recent_gaps(mid: int, last_n: int = 500) -> list[float]:
    fp = DIAG / f"machine_{mid}" / DAY / "samples.csv"
    if not fp.exists():
        return []
    rows = []
    with fp.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            mono = (row.get("mono") or "").strip()
            if mono:
                rows.append(float(mono))
    if len(rows) < 2:
        return []
    rows = rows[-last_n:]
    return [rows[i] - rows[i - 1] for i in range(1, len(rows)) if rows[i] > rows[i - 1]]


def summarize(name: str, vals: list[float]) -> str:
    if not vals:
        return f"{name}: no data"
    s = sorted(vals)
    return (
        f"{name}: n={len(vals)} p50={statistics.median(s):.0f} p95={s[int(len(s)*0.95)]:.0f} "
        f"max={s[-1]:.0f} >1s={sum(1 for v in vals if v>1000)} >5s={sum(1 for v in vals if v>5000)}"
    )


def main():
    print(f"Monitor start {datetime.now(TR).isoformat()} duration={DURATION_S}s poll={POLL_S}s")
    snap_lat: dict[int, list[float]] = defaultdict(list)
    snap_age: dict[int, list[float]] = defaultdict(list)
    snap_fail: dict[int, int] = defaultdict(int)
    live_lat: list[float] = []
    keeping_false = 0
    keeping_true = 0
    loop_ms_vals: list[float] = []
    frame_age_cam: dict[int, list[float]] = defaultdict(list)
    t_end = time.monotonic() + DURATION_S
    n = 0
    while time.monotonic() < t_end:
        n += 1
        for cam in (1, 2):
            r = fetch_snapshot(cam)
            if r.get("ok"):
                snap_lat[cam].append(r["latency_ms"])
                if r.get("frame_age_ms") is not None:
                    snap_age[cam].append(r["frame_age_ms"])
            else:
                snap_fail[cam] += 1
        if n % 4 == 0:  # every ~2s
            lv = fetch_live()
            live_lat.append(lv.get("latency_ms", 0))
            if lv.get("ok"):
                d = lv["data"]
                vis = d.get("vision") or {}
                if vis.get("loop_ms") is not None:
                    loop_ms_vals.append(float(vis["loop_ms"]))
                for c in d.get("cameras", []):
                    cid = int(c["id"])
                    if c.get("frame_age_ms") is not None:
                        frame_age_cam[cid].append(float(c["frame_age_ms"]))
                    if c.get("keeping_up") is True:
                        keeping_true += 1
                    elif c.get("keeping_up") is False:
                        keeping_false += 1
        if n % 120 == 0:
            print(f"  ... {int(time.monotonic() - (t_end - DURATION_S))}s elapsed")
        time.sleep(POLL_S)

    print(f"\n=== RESULTS {datetime.now(TR).isoformat()} ===")
    for cam in (1, 2):
        print(summarize(f"snapshot_latency_cam{cam}", snap_lat[cam]))
        print(summarize(f"snapshot_X-Frame-Age_cam{cam}", snap_age[cam]))
        print(f"snapshot_fail_cam{cam}: {snap_fail[cam]}")
    print(summarize("live_snapshot_latency", live_lat))
    print(summarize("vision_loop_ms", loop_ms_vals))
    for cam in (1, 2):
        print(summarize(f"api_frame_age_cam{cam}", frame_age_cam[cam]))
    print(f"keeping_up true={keeping_true} false={keeping_false}")
    for mid in (4, 5, 6):
        gaps = csv_recent_gaps(mid)
        gs = [g * 1000 for g in gaps]  # mono gaps to ms
        print(summarize(f"csv_mono_gap_ms_AF{mid}", gs))


if __name__ == "__main__":
    main()
