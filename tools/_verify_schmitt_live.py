#!/usr/bin/env python3
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

DB = "/home/pi/injection-monitor/backend/data/injection.db"

print("=== DB config ===")
con = sqlite3.connect(DB)
for r in con.execute(
    "SELECT id,name,counting_mode,closed_polarity,closed_ref,closed_hyst,learn_enabled "
    "FROM machines WHERE id IN (4,5,6)"
):
    print(r)

print("\n=== Live snapshot ===")
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=30) as resp:
        d = json.loads(resp.read())
except Exception as e:
    print("snapshot FAIL:", e)
    sys.exit(1)

for m in d.get("machines", []):
    if m.get("id") not in (4, 5, 6):
        continue
    print(
        f"  m{m['id']} {m.get('name')} state={m.get('state')} pos={m.get('position_01')} "
        f"mode={m.get('counting_mode')} ref_live={m.get('closed_ref_live')} "
        f"last_cycle={m.get('cycle_time_last')} emits={m.get('dbg_cycle_emit_count')}"
    )

print("\n=== Cycles since deploy (~11:24 TR) ===")
since = datetime(2026, 9, 2, 8, 24, tzinfo=timezone.utc).isoformat()
for mid in (4, 5, 6):
    rows = con.execute(
        "SELECT COUNT(*), AVG(cycle_time_s), MIN(cycle_time_s), MAX(cycle_time_s) "
        "FROM cycles WHERE machine_id=? AND t_end>=?",
        (mid, since),
    ).fetchone()
    x2 = con.execute(
        "SELECT COUNT(*) FROM cycles WHERE machine_id=? AND t_end>=? "
        "AND cycle_time_s>=28 AND cycle_time_s<40",
        (mid, since),
    ).fetchone()[0]
    avg = rows[1] if rows[1] is not None else 0
    print(f"  AF-{mid}: n={rows[0]} avg={avg:.1f}s min={rows[2]} max={rows[3]} 2x_band={x2}")

con.close()
