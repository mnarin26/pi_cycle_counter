#!/usr/bin/env python3
"""Check if AF-4 closed-band jitter crosses re-arm (exit) threshold."""
from __future__ import annotations

import csv
from pathlib import Path

fp = Path("/home/pi/injection-monitor/backend/logs/diag/machine_4/2026-09-02/samples.csv")
REF = 0.309
HYST = 0.05
ENTER = REF + HYST  # 0.359
EXIT = REF + 2 * HYST  # 0.409

# Scan post-deploy for closed dwell periods and how often pos exceeds EXIT while "in closed"
since = "2026-09-02T08:24:00"
last_pos = None
cross_exit_from_closed = 0
in_closed = False
samples_closed = 0
samples_closed_above_exit = 0
max_in_closed = 0.0
pairs = []  # brief excursions above EXIT then back

with fp.open(encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh, delimiter=";"):
        if (row.get("t_iso") or "") < since:
            continue
        if (row.get("k") or "").strip() == "stale_skip":
            continue
        p = (row.get("pos") or "").strip()
        if not p:
            continue
        pos = float(p)
        if pos <= ENTER:
            in_closed = True
            samples_closed += 1
            max_in_closed = max(max_in_closed, pos)
            if pos >= EXIT:
                samples_closed_above_exit += 1
        elif in_closed and pos >= EXIT:
            cross_exit_from_closed += 1
            in_closed = False
        if pos >= EXIT:
            in_closed = False

print(f"ref={REF} enter={ENTER:.3f} exit={EXIT:.3f}")
print(f"closed samples={samples_closed}  (should stay below exit)")
print(f"re-arm crossings from closed band={cross_exit_from_closed}")
print(f"(each re-arm enables another count on next closed enter)")

# Also: how often does a cycle have two closed enters within <12s?
# Approximate by counting closed enters with schmitt logic
armed = False
in_c = False
fires = []
t0 = None
last_fire = None
with fp.open(encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh, delimiter=";"):
        if (row.get("t_iso") or "") < since:
            continue
        if (row.get("k") or "").strip() == "stale_skip":
            continue
        mono = (row.get("mono") or "").strip()
        p = (row.get("pos") or "").strip()
        if not mono or not p:
            continue
        t, pos = float(mono), float(p)
        if t0 is None:
            t0 = t
        if pos >= EXIT:
            in_c = False
            armed = True
        elif pos <= ENTER:
            if not in_c:
                in_c = True
                if armed:
                    armed = False
                    dt = None if last_fire is None else t - last_fire
                    fires.append((t - t0, dt, pos))
                    last_fire = t

short = [f for f in fires if f[1] is not None and f[1] < 10]
print(f"\nschmitt replay fires={len(fires)} short_dt<10={len(short)}")
print("first short pairs:")
for f in short[:8]:
    print(f"  t+{f[0]:.1f}s dt={f[1]:.2f}s pos={f[2]:.3f}")
