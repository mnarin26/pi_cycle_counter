"""RTSP capture thread with reconnect, low-latency tuning, and backlog drain.

Why this file is fussy:
- OpenCV's default FFmpeg config buffers many frames; on a Raspberry Pi 3B that lags
  the live view by seconds and produces "old frame bursts" when the CPU catches up.
- We force `rtsp_transport=tcp` (more reliable on Wi-Fi) plus `nobuffer`, `low_delay`
  and a small `max_delay`, set buffersize to 1, and after every grab we drain any
  backlog frames so we always retrieve the freshest one.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

# CRITICAL: must be set BEFORE cv2 is imported the first time in this process.
# Idempotent here in case another module set partial flags.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;200000|reorder_queue_size;0",
)

import cv2  # noqa: E402  (env must be set first)
import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)


class RtspWorker(threading.Thread):
    def __init__(
        self,
        camera_id: int,
        rtsp_url: str,
        target_width: int = 640,
        frame_skip: int = 2,
        on_status: Callable[[str], None] | None = None,
        daemon: bool = True,
    ):
        super().__init__(daemon=daemon)
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url.strip()
        self.target_width = target_width
        self.frame_skip = max(1, frame_skip)
        self.on_status = on_status
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_mono: float = 0.0
        self._status = "disconnected"
        self._fps_ema = 0.0
        self._last_tick = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    def read_latest(self) -> np.ndarray | None:
        with self._lock:
            if self._latest is None:
                return None
            return self._latest.copy()

    def latest_age_ms(self) -> float:
        with self._lock:
            if self._latest_mono <= 0:
                return -1.0
            return (time.monotonic() - self._latest_mono) * 1000.0

    @property
    def status(self) -> str:
        return self._status

    @property
    def fps(self) -> float:
        return self._fps_ema

    def _set_status(self, s: str) -> None:
        self._status = s
        if self.on_status:
            self.on_status(s)

    def _open(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return None
        # Best effort: backend may ignore, but when honored it keeps only the newest frame.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self.rtsp_url:
                self._set_status("disconnected")
                time.sleep(1.0)
                continue
            cap = self._open()
            if cap is None:
                self._set_status("error")
                time.sleep(min(backoff, 30.0))
                backoff = min(backoff * 1.5, 30.0)
                continue
            backoff = 1.0
            self._set_status("ok")
            self._last_tick = time.monotonic()

            consecutive_fail = 0
            while not self._stop.is_set():
                ok = cap.grab()
                if not ok:
                    consecutive_fail += 1
                    if consecutive_fail > 10:
                        break
                    time.sleep(0.02)
                    continue
                consecutive_fail = 0

                # Drain backlog: grab additional frames quickly to skip past anything older
                # than "now". frame_skip-1 extra grabs guarantees we retrieve the freshest
                # frame the decoder has produced so far.
                for _ in range(max(0, self.frame_skip - 1)):
                    if not cap.grab():
                        break

                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    consecutive_fail += 1
                    if consecutive_fail > 10:
                        break
                    continue

                h, w = frame.shape[:2]
                if w > self.target_width and self.target_width > 0:
                    scale = self.target_width / float(w)
                    nh = int(h * scale)
                    frame = cv2.resize(frame, (self.target_width, nh), interpolation=cv2.INTER_AREA)

                now = time.monotonic()
                with self._lock:
                    self._latest = frame
                    self._latest_mono = now
                dt = now - self._last_tick
                self._last_tick = now
                if dt > 1e-6:
                    inst_fps = 1.0 / dt
                    self._fps_ema = self._fps_ema * 0.85 + inst_fps * 0.15

            try:
                cap.release()
            except Exception:
                pass
            self._set_status("disconnected")
