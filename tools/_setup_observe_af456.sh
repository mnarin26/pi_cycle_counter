#!/bin/bash
# Enable timed CSV diag for AF-4/5/6 until tomorrow 14:00 TR (noon analysis buffer).
set -e
DB=/home/pi/injection-monitor/backend/data/injection.db
# 2026-09-02 14:00 Europe/Istanbul = 11:00 UTC
UNTIL='2026-09-02T11:00:00.000Z'

python3 <<PY
import sqlite3
db = "$DB"
until = "$UNTIL"
con = sqlite3.connect(db)
for mid in (4, 5, 6):
    con.execute("UPDATE machines SET diag_until=? WHERE id=?", (until, mid))
con.commit()
for r in con.execute("SELECT id,name,diag_until FROM machines WHERE id IN (4,5,6)"):
    print("set", r)
con.close()
print("diag_until =", until, "(14:00 TR yarin)")
PY

echo "Kameralar acilinca 8000 config reload ~2sn icinde CSV yazmaya baslar."
echo "Ogle analizi: python3 /home/pi/injection-monitor/tools/_observe_af456_noon.py --day 2026-09-02"
