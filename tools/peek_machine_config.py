import json
import sys
import urllib.request

mid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
with urllib.request.urlopen("http://127.0.0.1:8000/api/machines", timeout=8) as r:
    ms = {m["id"]: m for m in json.loads(r.read().decode())}
m = ms.get(mid)
print("machine", json.dumps(m, indent=2) if m else "missing")

with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=8) as r:
    snap = json.loads(r.read().decode())
for sm in snap.get("machines", []):
    if int(sm.get("id", -1)) == mid:
        print("live", json.dumps(sm, indent=2))
        break
