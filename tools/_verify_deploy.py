#!/usr/bin/env python3
"""Quick post-deploy check on the Pi (read-only)."""
from __future__ import annotations

import sqlite3
import urllib.request

DB = "/home/pi/injection-monitor/backend/data/injection.db"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = {r[1] for r in con.execute("PRAGMA table_info(machines)")}
    print("has_min_prominence", "min_prominence" in cols)
    if "min_prominence" in cols:
        rows = con.execute(
            "SELECT id, name, min_prominence FROM machines ORDER BY id"
        ).fetchall()
        for r in rows:
            print("machine", r)
    con.close()

    for url in (
        "http://127.0.0.1:8000/api/live/snapshot",
        "http://127.0.0.1:8080/",
        "http://127.0.0.1:8000/api/machines",
    ):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read(200)
                print(url, r.status, body[:80])
        except Exception as e:
            print(url, "FAIL", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
