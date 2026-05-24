import app.db.session as s
from app.services.mold_matcher import replay_mold_history
from app.services.time_windows import resolve_window

s.get_engine()
db = s.SessionLocal()
start, end = resolve_window("weekly", None, None)
print("reprocess", start, end)
result = replay_mold_history(db, 4, start, end, "reprocess")
print(result)
db.commit()
db.close()
