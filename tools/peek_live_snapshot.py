import json
import sys
import urllib.request

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
with urllib.request.urlopen(f"http://{host}:8000/api/live/snapshot", timeout=8) as r:
    d = json.loads(r.read().decode())
rows = []
for m in d.get("machines", []):
    if m.get("state") == "DISABLED":
        continue
    rows.append(
        {
            "id": m.get("id"),
            "state": m.get("state"),
            "peak": m.get("peak"),
            "bg": m.get("background"),
            "fps": m.get("fps"),
        }
    )
print(json.dumps(rows[:20], indent=2))
