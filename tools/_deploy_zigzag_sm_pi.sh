#!/bin/bash
# Deploy peak/trough + jump_abs state_machine (+ orchestrator sync) to Pi and restart 8000.
# ONLY after morning OK — do not run overnight blindly.
set -e
IM=/home/pi/injection-monitor
SM="$IM/backend/app/vision/state_machine.py"
OR="$IM/backend/app/vision/orchestrator.py"
cp -a "$SM" "$SM.bak.$(date +%s)"
cp -a "$OR" "$OR.bak.$(date +%s)"
if [[ ! -f /tmp/state_machine_zigzag.py || ! -f /tmp/orchestrator_zigzag.py ]]; then
  echo "missing /tmp/state_machine_zigzag.py or /tmp/orchestrator_zigzag.py"
  exit 1
fi
cp /tmp/state_machine_zigzag.py "$SM"
cp /tmp/orchestrator_zigzag.py "$OR"
cd "$IM/backend"
pkill -f 'uvicorn app.main:app' || true
sleep 2
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/main8000.log 2>&1 &
sleep 5
curl -s --max-time 10 http://127.0.0.1:8000/api/health
echo
pgrep -af 'uvicorn app.main' | head -2
echo "deployed zigzag SM — verify AF-3/4/5"
