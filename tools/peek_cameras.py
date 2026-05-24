import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/cameras", timeout=8) as r:
    cams = json.loads(r.read().decode())
en = [c for c in cams if c.get("enabled")]
print("enabled_cameras", len(en))
for c in en:
    print(c["id"], c.get("name"), "url" if c.get("rtsp_url") else "NO_URL", c.get("rtsp_url", "")[:60])

with urllib.request.urlopen("http://127.0.0.1:8000/api/machines", timeout=8) as r:
    ms = json.loads(r.read().decode())
for m in ms:
    if m.get("enabled"):
        print("machine", m["id"], "cam", m.get("camera_id"), m.get("name"))
