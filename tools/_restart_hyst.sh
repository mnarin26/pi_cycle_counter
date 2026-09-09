#!/bin/bash
set -e
UV=/home/pi/injection-monitor/backend/.venv/bin/uvicorn
LOG=/home/pi/injection-monitor/backend/logs
APPDIR=/home/pi/injection-monitor/backend
mkdir -p "$LOG"
pkill -9 -f 'uvicorn app.main' 2>/dev/null || true
pkill -9 -f 'uvicorn admin_app' 2>/dev/null || true
sleep 2
# free ports if anything left
fuser -k 8000/tcp 2>/dev/null || true
fuser -k 8080/tcp 2>/dev/null || true
sleep 1
cd "$APPDIR"
nohup "$UV" app.main:app --host 0.0.0.0 --port 8000 >> "$LOG/main.log" 2>&1 &
echo "main_pid=$!"
nohup "$UV" admin_app:app --host 0.0.0.0 --port 8080 >> "$LOG/admin.log" 2>&1 &
echo "admin_pid=$!"
sleep 18
curl -s -m 25 -o /dev/null -w "8000:%{http_code}\n" http://127.0.0.1:8000/api/auth/login-mode || echo "8000:fail"
curl -s -m 8 -o /dev/null -w "8080:%{http_code}\n" http://127.0.0.1:8080/ || echo "8080:fail"
pgrep -af 'uvicorn app.main|uvicorn admin_app' || true
grep -n 'hysteresis' "$APPDIR/app/vision/state_machine.py" | head -5
grep -n 'id="hys"' "$APPDIR/admin_static/index.html" | head -2
PYTHONPATH="$APPDIR" "$APPDIR/.venv/bin/python" - <<'PY'
from app.db import session as s
from app.db.models import Machine
s.get_engine()
db = s.SessionLocal()
m4 = db.get(Machine, 4)
m3 = db.get(Machine, 3)
print("m3 hyst", m3.hysteresis, "deb", m3.debounce_ms, "stab", m3.stability_confirm_ms)
print("m4 hyst", m4.hysteresis, "deb", m4.debounce_ms, "stab", m4.stability_confirm_ms)
print("eff_eps_m4", max(0.0015, float(m4.hysteresis)*0.12))
PY
