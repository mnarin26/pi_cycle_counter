"""Quick local/Pi test for replay_mold_history import chain."""
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.services.mold_matcher import replay_mold_history
from app.services.time_windows import resolve_window

db = SessionLocal()
try:
    start, end = resolve_window("weekly", None, None)
    result = replay_mold_history(db, 4, start, end, "missing_only")
    db.commit()
    print("OK", result)
except Exception as e:
    db.rollback()
    print("ERR", type(e).__name__, e)
    raise
finally:
    db.close()
