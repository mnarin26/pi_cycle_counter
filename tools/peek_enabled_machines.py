import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/machines", timeout=8) as r:
    ms = json.loads(r.read().decode())
en = [m for m in ms if m.get("enabled")]
print("enabled_count", len(en))
print("enabled_ids", [m["id"] for m in en[:30]])
