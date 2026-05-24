import time
import app.db.session as s
from app.services.time_windows import resolve_window
from app.api.routers.analytics import _fetch_cycle_series, _aggregate_cycle_stats

s.get_engine()
db = s.SessionLocal()
start, end = resolve_window("weekly", None, None)

t0 = time.time()
stats = _aggregate_cycle_stats(db, 4, start, end)
t1 = time.time()
series, total, tr = _fetch_cycle_series(db, 4, start, end, 1200)
t2 = time.time()
# one day slice like viewport (~1.35d pad)
from datetime import timedelta
dstart = start + timedelta(days=2)
dend = dstart + timedelta(hours=36)
series2, total2, tr2 = _fetch_cycle_series(db, 4, dstart, dend, 1200)
t3 = time.time()

print("weekly stats cycles", stats["cycle_count"], "sec", round(t1 - t0, 3))
print("weekly series total", total, "shown", len(series), "sec", round(t2 - t1, 3))
print("1d viewport total", total2, "shown", len(series2), "sec", round(t3 - t2, 3))
db.close()
