#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB = Path("/home/pi/injection-monitor/backend/data/injection.db")
con = sqlite3.connect(DB)
print("=== CAMERAS ===")
for r in con.execute(
    "SELECT id,name,enabled,target_fps,target_width,status FROM cameras ORDER BY id"
):
    print(r)
print("\n=== ENABLED MACHINES ===")
rows = con.execute(
    "SELECT id,name,camera_id,diag_until FROM machines WHERE enabled=1 ORDER BY id"
).fetchall()
for r in rows:
    print(r)
print(f"\nenabled count: {len(rows)}")
by_cam = {}
for mid, name, cid, du in rows:
    by_cam.setdefault(cid, []).append((mid, name, du))
print("\nper camera:")
for cid, ms in sorted(by_cam.items()):
    diag = sum(1 for _, _, d in ms if d)
    print(f"  cam {cid}: {len(ms)} machines, diag_active={diag}")
con.close()

# config defaults
import sys
sys.path.insert(0, "/home/pi/injection-monitor/backend")
from app.config import settings
print("\n=== SETTINGS ===")
for k in ("frame_skip", "diag_jpeg_hz", "diag_jpeg_max_width", "diag_ring_seconds", "ws_broadcast_hz"):
    print(f"  {k}={getattr(settings, k, None)}")
