"""Inspect machine #4 mold assignments for production window (TR 03:40-17:00)."""
from collections import Counter
from datetime import datetime, timezone, timedelta

import app.db.session as s
from app.db.models import Cycle, Mold, Event, Machine

s.get_engine()
db = s.SessionLocal()
mid = 4

# TR UTC+3: 03:40 = 00:40 UTC, 17:00 = 14:00 UTC on same calendar day
latest = db.query(Cycle.t_end).filter(Cycle.machine_id == mid).order_by(Cycle.t_end.desc()).first()
if not latest:
    print("no cycles")
    raise SystemExit(0)

day = latest[0].date() if hasattr(latest[0], "date") else latest[0].date()
start = datetime(day.year, day.month, day.day, 0, 35, tzinfo=timezone.utc)
end = datetime(day.year, day.month, day.day, 14, 5, tzinfo=timezone.utc)

print(f"=== Machine #{mid} window {start} -> {end} UTC (TR +3) ===")
print(f"latest cycle: {latest[0]}")

rows = (
    db.query(
        Cycle.t_end,
        Cycle.cycle_time_s,
        Cycle.mold_id,
        Cycle.mold_name_snapshot,
        Cycle.is_counted,
        Cycle.exclude_reason,
    )
    .filter(Cycle.machine_id == mid, Cycle.t_end >= start, Cycle.t_end <= end)
    .order_by(Cycle.t_end.asc())
    .all()
)
print(f"total cycles in window: {len(rows)}")

# segment by mold changes / gaps
prev_t = None
segments = []
cur = {"start": None, "end": None, "mold_id": None, "mold_name": None, "count": 0, "ex": Counter()}
for r in rows:
    t, cs, mold_id, mname, counted, ex = r
    gap = (t - prev_t).total_seconds() if prev_t else 0
    if prev_t and gap >= 1200:
        if cur["count"]:
            segments.append(cur)
        cur = {"start": t, "end": t, "mold_id": mold_id, "mold_name": mname, "count": 0, "ex": Counter(), "gap_before": gap}
    if cur["start"] is None:
        cur["start"] = t
    if cur["mold_id"] != mold_id:
        if cur["count"]:
            segments.append(cur)
        cur = {
            "start": t,
            "end": t,
            "mold_id": mold_id,
            "mold_name": mname,
            "count": 0,
            "ex": Counter(),
            "gap_before": gap if prev_t else 0,
        }
    cur["end"] = t
    cur["count"] += 1
    if ex:
        cur["ex"][ex] += 1
    prev_t = t
if cur["count"]:
    segments.append(cur)

print("\n=== segments (gap>=20m or mold change) ===")
for i, seg in enumerate(segments[:20]):
    print(
        i,
        seg["start"].strftime("%H:%M:%S"),
        "->",
        seg["end"].strftime("%H:%M:%S"),
        "n=",
        seg["count"],
        "mold",
        seg["mold_id"],
        repr(seg["mold_name"]),
        "ex",
        dict(seg["ex"]),
        "gap_bef",
        round(seg.get("gap_before") or 0, 0),
    )

null_start = sum(1 for r in rows[:50] if r[3] is None and r[2] is None)
assigned_start = sum(1 for r in rows[:50] if r[2] is not None)
print(f"\nfirst 50: mold_id set={assigned_start}, both null={null_start}")

print("\n=== molds ===")
for m in db.query(Mold).order_by(Mold.id).all():
    print(m.id, repr(m.name), "avg", round(m.avg_cycle_s, 3), "tol", round(m.tolerance_s, 3), m.status)

evs = (
    db.query(Event.type, Event.created_at, Event.payload)
    .filter(Event.machine_id == mid, Event.created_at >= start, Event.created_at <= end)
    .order_by(Event.created_at.asc())
    .all()
)
print(f"\n=== events ({len(evs)}) ===")
for e in evs:
    print(e[0], e[1].strftime("%H:%M:%S"), (e[2] or "")[:200])

m4 = db.get(Machine, mid)
print("\ncurrent_mold_id:", m4.current_mold_id if m4 else None)

db.close()
