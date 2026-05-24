"""Benchmark machine_dashboard monthly response time."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.db.session as db_session
from app.api.routers.analytics import machine_dashboard
from app.db.session import get_engine, init_db

get_engine()
init_db()
db = db_session.SessionLocal()
t0 = time.perf_counter()
machine_dashboard(machine_id=2, db=db, range="monthly")
dt = time.perf_counter() - t0
print(f"monthly machine_id=2: {dt:.2f}s")
db.close()
