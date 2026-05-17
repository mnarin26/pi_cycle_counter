"""Vision processing loop.

This version uses the 1D **line probe** detector (see `line_pipeline.py`).
There are no ROI polygons, blob selection, CLAHE, morphology, or tracking
gates in the detection path anymore. Each machine has:

- `axis_p0` / `axis_p1` (normalized 0..1): the line endpoints. p0 is the
  "closed" end, p1 is the "open" end.
- `line_thickness`: perpendicular pixel window (max-pooled) for robustness
  to small jitter and motion blur.
- `threshold_mode` ("fixed" / "adaptive") + `threshold_min` (used as
  `prominence_min` in fixed mode) + `threshold_offset`.

The `roi_polygon` field is preserved in the DB for the admin UI drawing tool
but is no longer used by the detector.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Camera, Event, Machine
import app.db.session as db_session
from app.services.csv_logger import append_cycle_row
from app.services.cycle_tracker import CycleTracker
from app.services.mold_matcher import handle_cycle_completion
from app.services.playback_buffer import PlaybackBuffer
from app.vision.line_pipeline import line_peak_position
from app.vision.performance import PerformanceMonitor
from app.vision.rtsp_worker import RtspWorker
from app.vision.state_machine import ClampStateMachine, StateMachineConfig

logger = logging.getLogger(__name__)


@dataclass
class MachineRuntime:
    sm: ClampStateMachine = field(default_factory=ClampStateMachine)
    ct: CycleTracker = field(default_factory=CycleTracker)
    last_pos: float | None = None
    last_move_mono: float = field(default_factory=lambda: time.monotonic())
    no_move_warned: bool = False
    perf: PerformanceMonitor = field(default_factory=PerformanceMonitor)
    last_cycle_s: float | None = None


class VisionOrchestrator(threading.Thread):
    def __init__(self, out_queue: queue.Queue, daemon: bool = True):
        super().__init__(daemon=daemon)
        self.out_queue = out_queue
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.workers: dict[int, RtspWorker] = {}
        self.machine_rt: dict[int, MachineRuntime] = {}
        self.playback = PlaybackBuffer()
        self.snapshot: dict[str, Any] = {
            "machines": [],
            "cameras": [],
            "cpu_proxy": 0.0,
        }
        self._reload_at = 0.0

    def stop(self) -> None:
        self._stop.set()
        for w in self.workers.values():
            w.stop()

    def _reload_db(self, db: Session) -> tuple[list[Camera], list[Machine]]:
        return list(db.query(Camera).order_by(Camera.id)), list(db.query(Machine).order_by(Machine.id))

    def _ensure_worker(self, cam: Camera) -> RtspWorker:
        w = self.workers.get(cam.id)
        if w is None or w.rtsp_url != (cam.rtsp_url or "").strip():
            if w:
                w.stop()
                w.join(timeout=2.0)
            w = RtspWorker(
                cam.id,
                cam.rtsp_url or "",
                target_width=cam.target_width or 640,
                frame_skip=settings.frame_skip,
                on_status=lambda s, cid=cam.id: self._on_cam_status(cid, s),
            )
            w.start()
            self.workers[cam.id] = w
        return w

    def _on_cam_status(self, camera_id: int, status: str) -> None:
        if status != "ok":
            try:
                self.out_queue.put_nowait(
                    {
                        "type": "camera_event",
                        "camera_id": camera_id,
                        "status": status,
                    }
                )
            except queue.Full:
                pass

    def run(self) -> None:
        db_session.get_engine()
        while not self._stop.is_set():
            now = time.monotonic()
            db = db_session.SessionLocal()
            try:
                cameras, machines = self._reload_db(db)
                if now >= self._reload_at:
                    self._reload_at = now + 2.0
                    for c in cameras:
                        if c.enabled and c.rtsp_url:
                            self._ensure_worker(c)
                    for cid in list(self.workers.keys()):
                        if cid not in {c.id for c in cameras if c.enabled and c.rtsp_url}:
                            w = self.workers.pop(cid)
                            w.stop()

                machine_rows = {m.id: m for m in machines}
                for mid in list(self.machine_rt.keys()):
                    if mid not in machine_rows:
                        del self.machine_rt[mid]
                for m in machines:
                    if m.id not in self.machine_rt:
                        self.machine_rt[m.id] = MachineRuntime()
                    self._sync_machine_config(m, self.machine_rt[m.id])

                for m in machines:
                    if not m.enabled:
                        continue
                    cam = next((c for c in cameras if c.id == m.camera_id), None)
                    if not cam or not cam.enabled:
                        continue
                    w = self.workers.get(cam.id)
                    if not w:
                        continue
                    frame = w.read_latest()
                    if frame is None:
                        continue
                    t0 = time.monotonic()
                    self._process_machine_frame(db, m, frame, w)
                    self.machine_rt[m.id].perf.tick_process(time.monotonic() - t0)

                cache = getattr(self, "_machine_snapshot_cache", {})
                for m in machines:
                    if m.id not in cache:
                        cache[m.id] = {
                            "id": m.id,
                            "name": m.name,
                            "camera_id": m.camera_id,
                            "state": "DISABLED" if not m.enabled else "UNKNOWN",
                            "position_01": None,
                            "centroid": None,
                            "roi_bbox": None,
                            "cycle_time_last": None,
                            "mold_name": None,
                            "confidence": 0.0,
                            "fps": 0.0,
                            "process_ms": 0.0,
                            "threshold_mode": (m.threshold_mode or "fixed").lower(),
                            "threshold_active_min": m.threshold_min,
                            "threshold_active_max": m.threshold_max,
                            "threshold_offset": m.threshold_offset,
                            "peak": 0,
                            "background": 0,
                            "prominence": 0,
                            "segment_len": 0,
                            "line_thickness": m.line_thickness,
                            "reflector_len_min": m.reflector_len_min,
                            "reflector_len_max": m.reflector_len_max,
                        }
                self._machine_snapshot_cache = cache
            finally:
                db.close()

            self._publish_snapshot()
            time.sleep(0.02)

    def _sync_machine_config(self, m: Machine, rt: MachineRuntime) -> None:
        rt.sm.cfg = StateMachineConfig(
            open_1d=m.open_position_1d,
            closed_1d=m.closed_position_1d,
            hysteresis=m.hysteresis,
            debounce_ms=m.debounce_ms,
            stability_confirm_ms=m.stability_confirm_ms,
        )

    def _process_machine_frame(self, db: Session, m: Machine, frame, worker: RtspWorker) -> None:
        rt = self.machine_rt[m.id]

        result = line_peak_position(
            frame_bgr=frame,
            axis_p0_json=m.axis_p0,
            axis_p1_json=m.axis_p1,
            thickness_px=int(m.line_thickness or 7),
            threshold_mode=m.threshold_mode or "fixed",
            prominence_min_fixed=int(m.threshold_min or 0),
            prominence_offset=int(m.threshold_offset or 0),
            reflector_len_min=m.reflector_len_min,
            reflector_len_max=m.reflector_len_max,
        )

        pos: float | None = None
        centroid_xy: tuple[float, float] | None = None
        if result.found:
            pos = result.position_01
            centroid_xy = result.centroid_px
            rt.last_move_mono = time.monotonic()
            rt.no_move_warned = False
        else:
            if time.monotonic() - rt.last_move_mono > m.no_movement_timeout_s and not rt.no_move_warned:
                rt.no_move_warned = True
                try:
                    self.out_queue.put_nowait(
                        {
                            "type": "event_only",
                            "event": {
                                "type": "no_movement",
                                "machine_id": m.id,
                                "payload": f'{{"timeout_s":{m.no_movement_timeout_s}}}',
                            },
                        }
                    )
                except queue.Full:
                    pass

        now_ms = time.monotonic() * 1000.0
        confirmed = rt.sm.step(pos, now_ms)
        rt.last_pos = pos

        self.playback.push(m.id, pos, confirmed.value)

        cycle_s = rt.ct.on_confirmed(confirmed)
        if cycle_s is not None and cycle_s > 0.05:
            rt.last_cycle_s = cycle_s
            t_end = datetime.now(timezone.utc)
            t_start = t_end
            try:
                self.out_queue.put_nowait(
                    {
                        "type": "cycle_completed",
                        "machine_id": m.id,
                        "machine_name": m.name,
                        "cycle_s": cycle_s,
                        "t_start": t_start.isoformat(),
                        "t_end": t_end.isoformat(),
                        "confidence": float(result.prominence) / 255.0 if result.found else 0.0,
                    }
                )
            except queue.Full:
                logger.warning("vision queue full, dropping cycle")

        mold_name = None
        if m.current_mold_id:
            from app.db.models import Mold as MoldModel

            mold = db.get(MoldModel, m.current_mold_id)
            if mold:
                mold_name = mold.name

        confidence = min(1.0, result.prominence / 100.0) if result.found else 0.0

        with self._lock:
            self._machine_snapshot_cache = getattr(self, "_machine_snapshot_cache", {})
            self._machine_snapshot_cache[m.id] = {
                "id": m.id,
                "name": m.name,
                "camera_id": m.camera_id,
                "state": confirmed.value,
                "position_01": pos,
                "centroid": (
                    {"x": float(centroid_xy[0]), "y": float(centroid_xy[1])} if centroid_xy is not None else None
                ),
                "roi_bbox": None,
                "cycle_time_last": rt.last_cycle_s,
                "mold_name": mold_name,
                "confidence": confidence,
                "fps": worker.fps,
                "process_ms": rt.perf.process_ms_ema,
                "threshold_mode": (m.threshold_mode or "fixed").lower(),
                "threshold_active_min": int(result.active_threshold),
                "threshold_active_max": 255,
                "threshold_offset": int(m.threshold_offset or 0),
                "peak": int(result.peak),
                "background": int(result.background),
                "prominence": int(result.prominence),
                "segment_len": int(result.segment_len),
                "line_thickness": int(m.line_thickness or 7),
                "reflector_len_min": m.reflector_len_min,
                "reflector_len_max": m.reflector_len_max,
            }

    def _publish_snapshot(self) -> None:
        cache = getattr(self, "_machine_snapshot_cache", {})
        cams = []
        for cid, w in self.workers.items():
            cams.append({"id": cid, "status": w.status, "fps": w.fps})
        with self._lock:
            machines = sorted(cache.values(), key=lambda x: x["id"])
            self.snapshot = {
                "machines": machines,
                "cameras": cams,
                "cpu_proxy": sum(m.get("process_ms", 0) for m in machines) if machines else 0.0,
            }


def drain_cycle_queue_item(db: Session, item: dict, rolling: dict[int, list[float]]) -> None:
    if item["type"] == "cycle_completed":
        m = db.get(Machine, item["machine_id"])
        if not m:
            return
        t_end = datetime.fromisoformat(item["t_end"])
        t_start = datetime.fromisoformat(item["t_start"])
        handle_cycle_completion(
            db,
            m,
            float(item["cycle_s"]),
            t_start,
            t_end,
            rolling,
            None,
            float(item.get("confidence", 1.0)),
        )
        m2 = db.get(Machine, item["machine_id"])
        mold_name = None
        if m2 and m2.current_mold_id:
            from app.db.models import Mold as MoldModel

            mold = db.get(MoldModel, m2.current_mold_id)
            if mold:
                mold_name = mold.name
        append_cycle_row(
            settings.logs_dir,
            m.id,
            item.get("machine_name", m.name),
            float(item["cycle_s"]),
            "OPEN",
            mold_name,
            float(item.get("confidence", 1.0)),
        )
    elif item["type"] == "event_only":
        ev = item["event"]
        db.add(Event(type=ev["type"], machine_id=ev.get("machine_id"), payload=ev.get("payload")))
        db.commit()
    elif item["type"] == "camera_event":
        db.add(
            Event(
                type="camera_disconnect",
                machine_id=None,
                payload=f'{{"camera_id":{item["camera_id"]},"status":"{item["status"]}"}}',
            )
        )
        c = db.get(Camera, item["camera_id"])
        if c:
            c.status = item["status"]
        db.commit()
