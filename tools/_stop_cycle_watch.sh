#!/bin/bash
# Stop and disable legacy cycle-watch-ai stack (frees ~30-40 MB RAM on Pi).
# Requires sudo for systemd units.
set -e
for svc in cycle-watch-ai cycle-watch-admin cycle-watch-analysis cycle-watch-ap; do
  sudo systemctl stop "${svc}.service" 2>/dev/null || true
  sudo systemctl disable "${svc}.service" 2>/dev/null || true
done
pkill -f '/home/pi/cycle-watch-ai' 2>/dev/null || true
sleep 2
if pgrep -af cycle-watch >/dev/null 2>&1; then
  echo "WARN: cycle-watch processes still running:"
  pgrep -af cycle-watch || true
  exit 1
fi
echo "cycle-watch-ai stopped and disabled"
