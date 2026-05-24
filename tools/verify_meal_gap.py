"""Quick check: no cycles during meal window on machine 2."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import and_, select

import app.db.session as db_session
from app.db.session import get_engine
from app.db.models import Cycle

get_engine()
db = db_session.SessionLocal()
# Seed timestamps are UTC; 04:00 meal = 04:00–04:35 UTC (user örnek: 03:59:51 / 04:00:02)
start = datetime(2026, 5, 23, 3, 55, 0, tzinfo=timezone.utc)
end = datetime(2026, 5, 23, 4, 40, 0, tzinfo=timezone.utc)
meal_start = datetime(2026, 5, 23, 4, 0, 0, tzinfo=timezone.utc)
meal_end = datetime(2026, 5, 23, 4, 35, 0, tzinfo=timezone.utc)
rows = (
    db.execute(
        select(Cycle.t_end)
        .where(and_(Cycle.machine_id == 2, Cycle.t_end >= start, Cycle.t_end <= end))
        .order_by(Cycle.t_end)
    )
    .scalars()
    .all()
)
def _aware(ts):
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


in_meal = [ts for ts in rows if meal_start <= _aware(ts) < meal_end]
print("window count", len(rows), "during_meal", len(in_meal))
if in_meal:
    print("FAIL in meal:", in_meal[:5])
else:
    print("OK no cycles during 04:00-04:35")
for ts in rows[:2]:
    print(" first", ts)
for ts in rows[-2:]:
    print(" last", ts)
db.close()
