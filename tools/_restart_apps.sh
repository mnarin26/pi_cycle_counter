#!/bin/bash
set -e
# Restart 8000 vision + 8080 admin. Do not touch Tailscale.
cd /home/pi/injection-monitor/backend
mkdir -p logs

pkill -f 'uvicorn app.main:app' || true
pkill -f 'uvicorn admin_app:app' || true
sleep 3

setsid nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs/manual.log 2>&1 < /dev/null &
echo "spawned 8000 pid $!"
setsid nohup .venv/bin/uvicorn admin_app:app --host 0.0.0.0 --port 8080 >> logs/admin.log 2>&1 < /dev/null &
echo "spawned 8080 pid $!"
sleep 4
pgrep -af 'uvicorn' || true
