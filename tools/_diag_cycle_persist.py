#!/usr/bin/env python3
"""Diagnose: schmitt emits vs DB cycles vs queue/drain."""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")
DB = "/home/pi/injection-monitor/backend/data/injection.db"
LOG = Path("/home/pi/injection-monitor/backend/logs/main.log")


def main():
    now = datetime.now(timezone.utc)
    print("now", now.astimezone(TR).isoformat())

    with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=20) as r:
        snap = json.loads(r.read())
    print("cpu_proxy", snap.get("cpu_proxy"))
    for m in snap.get("machines", []):
        if m.get("id") in (4, 5, 6):
            print(
                f"live m{m['id']}: emits={m.get('dbg_cycle_emit_count')} "
                f"last={m.get('cycle_time_last')} mode={m.get('counting_mode')} "
                f"ref={m.get('closed_ref_live')} pos={m.get('position_01')} "
                f"state={m.get('state')}"
            )

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("\n=== last 8 cycles ANY time (AF-4/5/6) ===")
    for mid in (4, 5, 6):
        rows = con.execute(
            "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=? "
            "ORDER BY t_end DESC LIMIT 8",
            (mid,),
        ).fetchone()
        # print more
        rows = con.execute(
            "SELECT cycle_time_s, t_end FROM cycles WHERE machine_id=? "
            "ORDER BY t_end DESC LIMIT 8",
            (mid,),
        ).fetchall()
        print(f"AF-{mid}:")
        for cs, te in rows:
            print(f"  {te}  {cs}")

    print("\n=== cycle counts by hour today (UTC) ===")
    for mid in (4, 5, 6):
        rows = con.execute(
            "SELECT substr(t_end,1,13), COUNT(*) FROM cycles "
            "WHERE machine_id=? AND t_end LIKE '2026-09-02%' "
            "GROUP BY 1 ORDER BY 1",
            (mid,),
        ).fetchall()
        print(f"AF-{mid}: {rows}")

    print("\n=== total cycles today ===")
    for mid in (4, 5, 6):
        n = con.execute(
            "SELECT COUNT(*) FROM cycles WHERE machine_id=? AND t_end LIKE '2026-09-02%'",
            (mid,),
        ).fetchone()[0]
        print(f"AF-{mid}: {n}")

    # events?
    print("\n=== recent events ===")
    rows = con.execute(
        "SELECT type, machine_id, created_at FROM events ORDER BY id DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        print(" ", r)

    con.close()

    print("\n=== main.log tail (drain/queue/error) ===")
    if LOG.exists():
        text = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        keys = ("drain", "queue", "Error", "error", "exception", "drop", "schmitt", "locked")
        hits = [ln for ln in text[-2000:] if any(k in ln for k in keys)]
        for ln in hits[-30:]:
            print(ln)


if __name__ == "__main__":
    main()
