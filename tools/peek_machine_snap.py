import json
import sys
import urllib.request

mid = int(sys.argv[1]) if len(sys.argv) > 1 else 3
with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=8) as r:
    d = json.loads(r.read().decode())
for m in d.get("machines", []):
    if int(m.get("id", -1)) == mid:
        print(json.dumps(m, indent=2))
        break
else:
    print("not_in_snapshot", mid)
