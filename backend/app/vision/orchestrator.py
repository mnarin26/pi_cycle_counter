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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Camera, Cycle, Event, Machine
import app.db.session as db_session
from app.services.cycle_daily_log import append_cycle_to_daily_csv
from app.services.cycle_tracker import CycleTracker
from app.services.mold_matcher import handle_cycle_completion
from app.services.playback_buffer import PlaybackBuffer
from app.vision.line_pipeline import line_peak_position
from app.vision.performance import PerformanceMonitor
from app.vision.rtsp_worker import RtspWorker
from app.vision.state_machine import ClampStateMachine, StateMachineConfig

logger = logging.getLogger(__name__)

# After occlusion_grace_ms with no raw peak, wait this much longer then drop to UNKNOWN
# (avoids "OPEN" sticking when the reflector is gone but the state machine latched a zone).
_LOST_SIGNAL_EXTRA_S = 0.45


@dataclass
class MachineRuntime:
    sm: ClampStateMachine = field(default_factory=ClampStateMachine)
    ct: CycleTracker = field(default_factory=CycleTracker)
    last_pos: float | None = None
    last_move_mono: float = field(default_factory=lambda: time.monotonic())
    no_move_warned: bool = False
    perf: PerformanceMonitor = field(default_factory=PerformanceMonitor)
    last_cycle_s: float | None = None
    last_found_mono: float = 0.0
    hold_pos_01: float | None = None
    hold_centroid_xy: tuple[float, float] | None = None
    dbg_cycle_emit_count: int = 0
    dbg_last_confirmed: str = "UNKNOWN"


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
        self._cameras_cache: list[Camera] = []
        self._machines_cache: list[Machine] = []
        self._mold_names: dict[int, str] = {}

    def stop(self) -> None:
        self._stop.set()
        for w in self.workers.values():
            w.stop()

    def _reload_db(self, db: Session) -> tuple[list[Camera], list[Machine]]:
        return list(db.query(Camera).order_by(Camera.id)), list(db.query(Machine).order_by(Machine.id))

    def _ensure_worker(self, cam: Camera) -> RtspWorker:
        w = self.workers.get(cam.id)
        want_url = (cam.rtsp_url or "").strip()
        want_w = int(cam.target_width or 640)
        want_fps = int(cam.target_fps or 8)
        stale = (
            w is None
            or w.rtsp_url != want_url
            or w.target_width != want_w
            or w.target_fps != want_fps
        )
        if stale:
            if w:
                w.stop()
                w.join(timeout=2.0)
            w = RtspWorker(
                cam.id,
                want_url,
                target_width=want_w,
                target_fps=want_fps,
                frame_skip=settings.frame_skip,
                on_status=lambda s, cid=cam.id: self._on_cam_status(cid, s),
            )
            w.start()
            self.workers[cam.id] = w
        return w

    def _on_cam_status(self, camera_id: int, status: str) -> None:
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

    def _reload_config_from_db(self) -> None:
        db = db_session.SessionLocal()
        try:
            cameras, machines = self._reload_db(db)
            self._cameras_cache = cameras
            self._machines_cache = machines
            from app.db.models import Mold as MoldModel

            mold_ids = {m.current_mold_id for m in machines if m.current_mold_id}
            self._mold_names = {}
            if mold_ids:
                for mold in db.query(MoldModel).filter(MoldModel.id.in_(mold_ids)).all():
                    self._mold_names[mold.id] = mold.name
            for c in cameras:
                if c.enabled and c.rtsp_url:
                    self._ensure_worker(c)
            enabled_cam_ids = {c.id for c in cameras if c.enabled and c.rtsp_url}
            for cid in list(self.workers.keys()):
                if cid not in enabled_cam_ids:
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
            cache = getattr(self, "_machine_snapshot_cache", {})
            enabled_ids = {m.id for m in machines if m.enabled}
            for mid in list(cache.keys()):
                if mid not in enabled_ids:
                    del cache[mid]
            self._machine_snapshot_cache = cache
        finally:
            db.close()

    def run(self) -> None:
        db_session.get_engine()
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now >= self._reload_at or not self._machines_cache:
                    self._reload_at = now + 2.0
                    self._reload_config_from_db()

                cameras = self._cameras_cache
                machines = self._machines_cache
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
                    self._process_machine_frame(m, frame, w)
                    self.machine_rt[m.id].perf.tick_process(time.monotonic() - t0)
            except Exception as e:
                logger.exception("vision loop failed: %s", e)
                time.sleep(0.25)

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
        stability_s = max(0.5, float(m.stability_confirm_ms or 500) / 1000.0)
        rt.ct.unknown_grace_s = max(settings.cycle_unknown_grace_s, stability_s * 6.0)
        rt.ct.unknown_grace_after_extreme_s = settings.cycle_unknown_grace_after_extreme_s
        rt.ct.endpoint_margin = settings.cycle_endpoint_margin
        rt.ct.min_travel_range = settings.cycle_min_travel_range

    def _process_machine_frame(self, m: Machine, frame, worker: RtspWorker) -> None:
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

        # Extra guard: line_pipeline can still accept a single-sample glitch as "found";
        # that keeps last_found_mono fresh and locks OPEN/CLOSED. Real stripe is wider post-blur.
        effective_found = bool(result.found)
        if effective_found and result.segment_len == 1 and result.prominence < 42:
            effective_found = False

        pos: float | None = None
        centroid_xy: tuple[float, float] | None = None
        occlusion_hold = False
        now_mono = time.monotonic()
        if effective_found:
            pos = result.position_01
            centroid_xy = result.centroid_px
            rt.last_move_mono = now_mono
            rt.last_found_mono = now_mono
            rt.hold_pos_01 = pos
            rt.hold_centroid_xy = centroid_xy
            rt.no_move_warned = False
        else:
            grace_s = max(0.0, float(m.occlusion_grace_ms or 0) / 1000.0)
            if (
                rt.hold_pos_01 is not None
                and rt.last_found_mono > 0
                and (now_mono - rt.last_found_mono) <= grace_s
            ):
                occlusion_hold = True
                pos = rt.hold_pos_01
                centroid_xy = rt.hold_centroid_xy
            if now_mono - rt.last_move_mono > m.no_movement_timeout_s and not rt.no_move_warned:
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

        if (
            not effective_found
            and not occlusion_hold
            and rt.last_found_mono > 0
            and (now_mono - rt.last_found_mono)
            > max(0.0, float(m.occlusion_grace_ms or 0) / 1000.0) + _LOST_SIGNAL_EXTRA_S
        ):
            rt.sm.reset()
            rt.hold_pos_01 = None
            rt.hold_centroid_xy = None
            rt.last_found_mono = 0.0
            pos = None
            centroid_xy = None

        now_ms = time.monotonic() * 1000.0
        confirmed = rt.sm.step(pos, now_ms)
        rt.last_pos = pos
        # #region agent log
        if m.id == 3 and confirmed.value != rt.dbg_last_confirmed:
            logger.info(
                "[DBG][H9] machine_state_transition mid=%s from=%s to=%s raw_found=%s eff_found=%s pos=%s prom=%s seg=%s",
                m.id,
                rt.dbg_last_confirmed,
                confirmed.value,
                result.found,
                effective_found,
                pos,
                result.prominence,
                result.segment_len,
            )
        rt.dbg_last_confirmed = confirmed.value
        # #endregion

        self.playback.push(m.id, pos, confirmed.value)

        track_pos = pos if pos is not None else rt.hold_pos_01
        cycle_s = rt.ct.on_confirmed(confirmed, track_pos)
        if cycle_s is not None and cycle_s > 0.05:
            rt.last_cycle_s = cycle_s
            rt.dbg_cycle_emit_count += 1
            # #region agent log
            if m.id == 3:
                logger.info(
                    "[DBG][H6/H7] cycle_emit mid=%s cycle_s=%.4f emit_count=%s state=%s pos=%s",
                    m.id,
                    cycle_s,
                    rt.dbg_cycle_emit_count,
                    confirmed.value,
                    pos,
                )
            # #endregion
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
                        "confidence": float(result.prominence) / 255.0 if effective_found else 0.0,
                    }
                )
                # #region agent log
                if m.id == 3:
                    logger.info(
                        "[DBG][H11] cycle_enqueued mid=3 qid=%s qsize_after_put=%s emit_count=%s",
                        id(self.out_queue),
                        self.out_queue.qsize(),
                        rt.dbg_cycle_emit_count,
                    )
                # #endregion
            except queue.Full:
                logger.warning("vision queue full, dropping cycle")

        mold_name = self._mold_names.get(m.current_mold_id) if m.current_mold_id else None

        confidence = min(1.0, result.prominence / 100.0) if effective_found else 0.0

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
                "occlusion_hold": occlusion_hold,
                "dbg_cycle_emit_count": int(rt.dbg_cycle_emit_count),
            }

    def _publish_snapshot(self) -> None:
        cache = getattr(self, "_machine_snapshot_cache", {})
        cams = []
        for cid, w in self.workers.items():
            age_ms = w.latest_age_ms()
            status = w.status
            if 0 <= age_ms < 10_000:
                status = "ok"
            cams.append({"id": cid, "status": status, "fps": w.fps})
        with self._lock:
            machines = sorted(cache.values(), key=lambda x: x["id"])
            self.snapshot = {
                "machines": machines,
                "cameras": cams,
                "cpu_proxy": sum(m.get("process_ms", 0) for m in machines) if machines else 0.0,
            }


def drain_cycle_queue_item(db: Session, item: dict, rolling: dict[int, list[float]]) -> None:
    if item["type"] == "cycle_completed":
        # #region agent log
        if int(item.get("machine_id", -1)) == 3:
            logger.warning("[DBG][H13] drain_enter_cycle mid=3")
        # #endregion
        m = db.get(Machine, item["machine_id"])
        # #region agent log
        if int(item.get("machine_id", -1)) == 3:
            logger.info("[DBG][H13] drain_after_get_machine mid=3 exists=%s", bool(m))
        # #endregion
        if not m:
            return
        # #region agent log
        if int(item.get("machine_id", -1)) == 3:
            before_cnt = db.query(Machine).filter(Machine.id == 3).count()
            logger.info(
                "[DBG][H7] drain_cycle_start mid=%s cycle_s=%s before_machine_exists=%s",
                item.get("machine_id"),
                item.get("cycle_s"),
                before_cnt,
            )
        # #endregion
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
        cycle = (
            db.query(Cycle)
            .filter(Cycle.machine_id == m.id, Cycle.t_end == t_end)
            .order_by(Cycle.id.desc())
            .first()
        )
        if cycle:
            append_cycle_to_daily_csv(
                settings.logs_dir,
                item.get("machine_name", m.name),
                cycle,
            )
        # #region agent log
        if int(item.get("machine_id", -1)) == 3:
            ccount = db.execute(text("SELECT COUNT(*) FROM cycles WHERE machine_id=3")).scalar()  # type: ignore[arg-type]
            logger.info("[DBG][H7] drain_cycle_done mid=3 cycles_count_now=%s", ccount)
        # #endregion
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
