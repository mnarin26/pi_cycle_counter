#!/usr/bin/env python3
"""Quick Pi verify for probe-info + live process_ms."""
from __future__ import annotations

import json
import urllib.request

HDR = {"X-Injection-Admin-Proxy": "loopback-proxy"}


def get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> None:
    d = get("http://127.0.0.1:8000/api/machines/1/probe-info", HDR)
    print(
        "probe",
        d.get("machine_name"),
        "peak",
        d.get("peak"),
        "bg",
        d.get("background"),
        "delta",
        d.get("delta"),
        "len",
        d.get("segment_len"),
        "band",
        bool(d.get("brightness_band")),
        "along",
        d.get("band_along"),
        "thick",
        d.get("band_thickness"),
        "jpg_len",
        len(d.get("brightness_preview_jpg_b64") or ""),
    )
    snap = get("http://127.0.0.1:8000/api/live/snapshot")
    cams = snap.get("cameras") or []
    print(
        "cams",
        [
            (c.get("id"), round(float(c.get("total_ms") or 0), 1), c.get("keeping_up"))
            for c in cams
        ],
    )
    ms = snap.get("machines") or []
    print(
        "proc",
        [
            (
                m.get("id"),
                m.get("name"),
                round(float(m.get("process_ms") or 0), 1),
                round(float(m.get("probe_ms") or 0), 1),
            )
            for m in ms[:8]
        ],
    )


if __name__ == "__main__":
    main()
