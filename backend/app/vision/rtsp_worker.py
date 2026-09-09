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
        target_fps: int = 8,
        frame_skip: int = 2,
        on_status: Callable[[str], None] | None = None,
        daemon: bool = True,
    ):
        super().__init__(daemon=daemon)
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url.strip()
        self.target_width = max(160, int(target_width or 640))
        self.target_fps = max(1, min(30, int(target_fps or 8)))
        self.frame_skip = max(1, frame_skip)
        self.on_status = on_status
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_mono: float = 0.0
        # Pre-encoded JPEG for /snapshot.jpg — avoids imencode on the API event loop.
        self._jpeg: bytes | None = None
        self._jpeg_mono: float = 0.0
        self._jpeg_last_enc = 0.0
        self._jpeg_min_interval_s = 0.25  # ~4 Hz max encode cost
        self._jpeg_quality = 65
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

    def read_latest_jpeg(self) -> tuple[bytes | None, float]:
        """Cached JPEG bytes + age_ms. Safe to serve from the HTTP thread."""
        with self._lock:
            if not self._jpeg:
                return None, -1.0
            age_ms = (time.monotonic() - self._jpeg_mono) * 1000.0 if self._jpeg_mono > 0 else -1.0
            return self._jpeg, age_ms

    def publish_jpeg(self, data: bytes, mono: float | None = None) -> None:
        """Store a JPEG produced off the grab thread (snapshot path)."""
        with self._lock:
            self._jpeg = data
            self._jpeg_mono = float(mono if mono is not None else time.monotonic())
            self._jpeg_last_enc = self._jpeg_mono

    def read_latest_gray(self) -> tuple[np.ndarray | None, float, float]:
        """One BGR→GRAY; convert outside the frame lock so RTSP grab is not stalled.

        Returns:
            (gray, frame_mono, age_ms). gray is None when no frame yet.
        """
        with self._lock:
            if self._latest is None:
                return None, 0.0, -1.0
            mono = float(self._latest_mono)
            age_ms = (time.monotonic() - mono) * 1000.0 if mono > 0 else -1.0
            bgr = self._latest
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return gray, mono, age_ms

    def latest_mono(self) -> float:
        """Monotonic timestamp of the newest frame (0.0 when none yet).

        Cheap (lock + float read): lets the process worker detect "new frame?"
        without paying for a BGR→GRAY conversion.
        """
        with self._lock:
            return float(self._latest_mono)

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
                loop_start = time.monotonic()
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
                # JPEG cache is refreshed lazily from snapshot path — never on the
                # grab loop (imencode here stole CPU and made pos samples jumpy).
                dt = now - self._last_tick
                self._last_tick = now
                if dt > 1e-6:
                    inst_fps = 1.0 / dt
                    self._fps_ema = self._fps_ema * 0.85 + inst_fps * 0.15

                # target_fps caps how often we process frames on the Pi (not the camera encoder rate)
                want_dt = 1.0 / float(self.target_fps)
                spent = time.monotonic() - loop_start
                if spent < want_dt:
                    time.sleep(want_dt - spent)

            try:
                cap.release()
            except Exception:
                pass
            self._set_status("disconnected")
