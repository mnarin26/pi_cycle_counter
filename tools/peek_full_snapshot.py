import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/live/snapshot", timeout=8) as r:
    d = json.loads(r.read().decode())
ms = d.get("machines", [])
print("machine_count", len(ms))
for m in ms[:20]:
    print(
        m.get("id"),
        m.get("state"),
        m.get("peak"),
        m.get("background"),
        m.get("fps"),
        m.get("camera_id"),
    )
cams = d.get("cameras", [])
print("camera_count", len(cams))
for c in cams:
    print("cam", c.get("id"), c.get("status"), c.get("fps"))
