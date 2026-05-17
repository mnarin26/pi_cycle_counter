"""Tiny diagnostic: subscribe to /ws snapshot for N seconds and dump centroids."""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import time


def grab(host: str, port: int) -> dict | None:
    s = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET /ws HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)

    def read_frame() -> bytes | None:
        h = s.recv(2)
        if len(h) < 2:
            return None
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack("!H", s.recv(2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", s.recv(8))[0]
        data = b""
        while len(data) < ln:
            chunk = s.recv(ln - len(data))
            if not chunk:
                break
            data += chunk
        return data

    out: dict | None = None
    for _ in range(3):
        d = read_frame()
        if not d:
            break
        try:
            msg = json.loads(d.decode("utf-8", "ignore"))
        except Exception:
            continue
        if msg.get("type") == "snapshot":
            out = msg.get("data")
            break
    s.close()
    return out


def main() -> int:
    host = os.environ.get("PI_HOST", "127.0.0.1")
    port = int(os.environ.get("PI_PORT", "8000"))
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    ids_filter = {1, 2, 3}
    if len(sys.argv) > 3:
        ids_filter = {int(x) for x in sys.argv[3].split(",")}
    for i in range(iters):
        snap = grab(host, port)
        if not snap:
            print(f"t={i}s  <no snapshot>")
        else:
            parts = []
            for m in snap.get("machines", []):
                if m["id"] not in ids_filter:
                    continue
                c = m.get("centroid")
                cs = "None" if not c else "({},{})".format(int(c["x"]), int(c["y"]))
                parts.append(
                    "#{} {:>8} thr>={} pos={} ctr={} conf={}".format(
                        m["id"],
                        m["state"],
                        m.get("threshold_active_min"),
                        m.get("position_01"),
                        cs,
                        round(m.get("confidence", 0), 2),
                    )
                )
            print("t={}s  {}".format(i, " | ".join(parts)))
        time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
