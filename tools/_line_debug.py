"""Pi-side debugger for the 1D line-probe detector.

Pulls a fresh snapshot from each camera and runs the line probe with the
machine's current settings; prints profile stats and the would-be detection.
"""

from __future__ import annotations

import json
import sys
import urllib.request

import cv2
import numpy as np

BASE = "http://127.0.0.1:8000"


def fetch_snapshot(camera_id: int):
    try:
        data = urllib.request.urlopen(f"{BASE}/api/cameras/{camera_id}/snapshot.jpg", timeout=5).read()
    except Exception as e:
        print(f"  cam {camera_id} snapshot error: {e}")
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def main() -> int:
    sys.path.insert(0, "/home/pi/injection-monitor/backend")
    from app.vision.line_pipeline import line_peak_position, sample_line_profile  # noqa: E402

    machines = json.loads(urllib.request.urlopen(f"{BASE}/api/machines", timeout=5).read().decode())
    cameras = json.loads(urllib.request.urlopen(f"{BASE}/api/cameras", timeout=5).read().decode())
    cam_by_id = {c["id"]: c for c in cameras}

    only_ids = set(int(x) for x in sys.argv[1].split(",")) if len(sys.argv) > 1 else None

    frame_cache: dict[int, np.ndarray] = {}
    for m in machines:
        if only_ids and m["id"] not in only_ids:
            continue
        cam = cam_by_id.get(m["camera_id"])
        if not cam:
            continue
        if cam["id"] not in frame_cache:
            f = fetch_snapshot(cam["id"])
            if f is None:
                continue
            frame_cache[cam["id"]] = f
        frame = frame_cache[cam["id"]]
        h, w = frame.shape[:2]

        p0 = json.loads(m["axis_p0"])
        p1 = json.loads(m["axis_p1"])
        line_len = ((p1[0] - p0[0]) * w) ** 2 + ((p1[1] - p0[1]) * h) ** 2
        line_len = line_len ** 0.5
        thickness = int(m.get("line_thickness", 7))
        profile, sx, sy = sample_line_profile(
            frame,
            (p0[0] * w, p0[1] * h),
            (p1[0] * w, p1[1] * h),
            thickness_px=thickness,
        )
        p_arr = profile.astype(np.float32) if profile.size else np.zeros(1, dtype=np.float32)
        v_min = int(p_arr.min())
        v_max = int(p_arr.max())
        med = int(np.median(p_arr))
        p98 = int(np.percentile(p_arr, 98.0))

        result = line_peak_position(
            frame_bgr=frame,
            axis_p0_json=m["axis_p0"],
            axis_p1_json=m["axis_p1"],
            thickness_px=thickness,
            threshold_mode=m["threshold_mode"],
            prominence_min_fixed=int(m["threshold_min"]),
            prominence_offset=int(m.get("threshold_offset", 0)),
            reflector_len_min=m.get("reflector_len_min"),
            reflector_len_max=m.get("reflector_len_max"),
        )

        print(
            f"#{m['id']:>2} {m['name']:<12} mode={m['threshold_mode']:<8} "
            f"line_len={int(line_len)} thickness={thickness} N={profile.size} "
            f"min={v_min} med={med} p98={p98} max={v_max} | "
            f"thr={result.active_threshold:<3} prom={result.prominence:<3} seg={result.segment_len:<3} "
            f"found={result.found} pos={result.position_01} "
            f"len_win=({m.get('reflector_len_min')},{m.get('reflector_len_max')})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
