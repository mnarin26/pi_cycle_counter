"""One-shot: print peak/bg from main app WebSocket snapshot."""
import json
import sys

try:
    import websocket
except ImportError:
    print("pip install websocket-client", file=sys.stderr)
    raise

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
ws = websocket.create_connection(f"ws://{host}:8000/ws", timeout=8)
msg = json.loads(ws.recv())
ws.close()
machines = msg.get("data", {}).get("machines", [])
rows = []
for m in machines:
    if m.get("state") == "DISABLED":
        continue
    rows.append(
        {
            "id": m.get("id"),
            "state": m.get("state"),
            "peak": m.get("peak"),
            "bg": m.get("background"),
            "prom": m.get("prominence"),
            "fps": m.get("fps"),
        }
    )
print(json.dumps(rows[:15], indent=2))
