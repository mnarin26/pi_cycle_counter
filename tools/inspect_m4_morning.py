from datetime import datetime, timezone
from collections import Counter

import app.db.session as dbmod
from app.db.models import Cycle, Mold, Event, Machine

dbmod.get_engine()
db = dbmod.SessionLocal()
mid = 4

latest = db.query(Cycle.t_end).filter(Cycle.machine_id == mid).order_by(Cycle.t_end.desc()).first()
print("latest", latest[0] if latest else None)

# last 7 days window
from sqlalchemy import func

rows = (
    db.query(
        Cycle.t_end,
        Cycle.cycle_time_s,
        Cycle.mold_id,
        Cycle.mold_name_snapshot,
        Cycle.is_counted,
        Cycle.exclude_reason,
    )
    .filter(Cycle.machine_id == mid)
    .order_by(Cycle.t_end.asc())
    .all()
)
print("total cycles", len(rows))

# find gaps >= 20 min
gaps = []
for i in range(1, len(rows)):
    gap = (rows[i][0] - rows[i - 1][0]).total_seconds()
    if gap >= 1200:
        gaps.append((rows[i - 1][0], rows[i][0], gap))
print("gaps >=20min", len(gaps))
for g in gaps[:15]:
    print(" gap", g[0], "->", g[1], "sec", round(g[2]))

# focus: cycles between 00:30 and 14:30 UTC on days that have morning production
# (03:40-17:00 TR = UTC+3)
print("\n=== molds ===")
for m in db.query(Mold).order_by(Mold.id).all():
    print(m.id, repr(m.name), "avg", m.avg_cycle_s, "tol", m.tolerance_s, m.status)

# per-day morning segment analysis
from itertools import groupby

def day_key(ts):
    return ts.date()

by_day = {}
for r in rows:
    d = r[0].date()
    by_day.setdefault(d, []).append(r)

def _aware(ts):
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts

for day in sorted(by_day)[-3:]:
  day_rows = by_day[day]
  start = datetime(day.year, day.month, day.day, 0, 30, tzinfo=timezone.utc)
  end = datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc)
  seg = [r for r in day_rows if start <= _aware(r[0]) <= end]
  if not seg:
    continue
  print(f"\n=== DAY {day} segment 03:30-17:30 TR count={len(seg)} ===")
  # first 50 cycles detail
  for r in seg[:45]:
    tr = r[0].astimezone(timezone.utc).replace(tzinfo=None)  # show UTC; user knows +3
    print(r[0].strftime("%H:%M:%S"), f"{r[1]:.3f}s", "mold", r[2], repr(r[3]), "cnt", r[4], "ex", r[5])
  if len(seg) > 45:
    print("...")
  cnt_mold = Counter((r[2], r[3]) for r in seg if r[4])
  cnt_ex = Counter(r[5] for r in seg if not r[4])
  null_start = sum(1 for r in seg[:40] if r[4] and r[2] is None)
  print("first40 unassigned counted", null_start)
  print("counted molds", cnt_mold.most_common())
  print("exclude reasons", cnt_ex.most_common())
  evs = (
    db.query(Event.type, Event.created_at, Event.payload)
    .filter(Event.machine_id == mid, Event.created_at >= start, Event.created_at <= end)
    .order_by(Event.created_at.asc())
    .all()
  )
  print("events", len(evs))
  for e in evs[:20]:
    print(" ", e[0], e[1].strftime("%H:%M:%S"), (e[2] or "")[:150])

# first x1 assignment on 2026-05-23
day_rows = by_day.get(__import__("datetime").date(2026, 5, 23), [])
first_x1 = next((r for r in day_rows if r[2] == 6), None)
if first_x1:
    print("\nfirst mold_id=6 (x1) at", first_x1[0], "cycle_s", first_x1[1])
    idx = day_rows.index(first_x1)
    pre = day_rows[:idx]
    print(
        "cycles before first x1:",
        len(pre),
        "excluded unknown:",
        sum(1 for r in pre if r[5] == "unknown_or_mold_change"),
        "counted unassigned:",
        sum(1 for r in pre if r[4] and r[2] is None),
    )

db.close()
