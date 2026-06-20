from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
import app.db.session as db_session
from app.api.routers import analytics, calibration, cameras, events, machines, molds, settings as settings_router
from app.vision.orchestrator import VisionOrchestrator, drain_cycle_queue_item
from app.ws.hub import Hub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    db_session.init_db()
    q: queue.Queue = queue.Queue(maxsize=settings.vision_queue_max)
    app.state.vision_queue = q
    app.state.rolling_cycles = {}
    orch = VisionOrchestrator(q)
    orch.start()
    app.state.vision = orch
    app.state.ws_hub = Hub()
    # #region agent log
    try:
        logger.info(
            "[DBG][H14] drain_func_file=%s first_line=%s has_h13=%s",
            drain_cycle_queue_item.__code__.co_filename,
            drain_cycle_queue_item.__code__.co_firstlineno,
            "[DBG][H13]" in inspect.getsource(drain_cycle_queue_item),
        )
    except Exception as e:
        logger.warning("[DBG][H14] drain_func_introspect_failed err=%s", e)
    # #endregion

    stop_drain = asyncio.Event()

    async def drain_loop():
        while not stop_drain.is_set():
            try:
                while not stop_drain.is_set():
                    item = q.get_nowait()
                    # #region agent log
                    if int(item.get("machine_id", -1)) == 3:
                        logger.info(
                            "[DBG][H11] drain_loop_got_item type=%s mid=%s qid=%s qsize_after_get=%s",
                            item.get("type"),
                            item.get("machine_id"),
                            id(q),
                            q.qsize(),
                        )
                    # #endregion
                    # #region agent log
                    if int(item.get("machine_id", -1)) == 3:
                        logger.info("[DBG][H15] before_sessionlocal mid=3")
                    # #endregion
                    db = db_session.SessionLocal()
                    # #region agent log
                    if int(item.get("machine_id", -1)) == 3:
                        logger.info("[DBG][H15] after_sessionlocal mid=3")
                    # #endregion
                    try:
                        drain_cycle_queue_item(db, item, app.state.rolling_cycles)
                        # #region agent log
                        if int(item.get("machine_id", -1)) == 3 and item.get("type") == "cycle_completed":
                            logger.info("[DBG][H11] drain_loop_cycle_persist_done mid=3")
                        # #endregion
                    except Exception as e:
                        logger.exception("drain item failed: %s", e)
                    finally:
                        db.close()
            except queue.Empty:
                pass
            await asyncio.sleep(0.05)

    async def broadcast_loop():
        interval = 1.0 / max(1.0, settings.ws_broadcast_hz)
        while not stop_drain.is_set():
            snap = app.state.vision.snapshot
            msg = json.dumps({"type": "snapshot", "data": snap})
            await app.state.ws_hub.broadcast(msg)
            await asyncio.sleep(interval)

    drain_task = asyncio.create_task(drain_loop())
    bcast_task = asyncio.create_task(broadcast_loop())

    async def retention_loop():
        from app.services.data_retention import run_data_retention

        while not stop_drain.is_set():
            db = db_session.SessionLocal()
            try:
                stats = run_data_retention(db, settings.logs_dir)
                logger.info("Veri saklama temizliği: %s", stats)
            except Exception as e:
                logger.exception("Veri saklama hatası: %s", e)
            finally:
                db.close()
            try:
                await asyncio.wait_for(stop_drain.wait(), timeout=86400.0)
                break
            except asyncio.TimeoutError:
                pass

    retention_task = asyncio.create_task(retention_loop())
    yield
    stop_drain.set()
    drain_task.cancel()
    bcast_task.cancel()
    retention_task.cancel()
    orch.stop()
    orch.join(timeout=3.0)


app = FastAPI(title="Injection Monitor", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router, prefix="/api/cameras", tags=["cameras"])
app.include_router(machines.router, prefix="/api/machines", tags=["machines"])
app.include_router(molds.router, prefix="/api/molds", tags=["molds"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(calibration.router, prefix="/api/calibration", tags=["calibration"])


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/live/snapshot")
def live_snapshot():
    """Current vision snapshot (same payload as WebSocket)."""
    return app.state.vision.snapshot


@app.post("/api/debug/fake_cycle")
def debug_fake_cycle(
    machine_id: int = Query(3),
    cycle_s: float = Query(2.5),
):
    q: queue.Queue = app.state.vision_queue
    now = datetime.now(timezone.utc)
    item = {
        "type": "cycle_completed",
        "machine_id": machine_id,
        "machine_name": f"Machine {machine_id}",
        "cycle_s": cycle_s,
        "t_start": now.isoformat(),
        "t_end": now.isoformat(),
        "confidence": 0.95,
    }
    q.put_nowait(item)
    # #region agent log
    logger.info(
        "[DBG][H12] fake_cycle_enqueued mid=%s qid=%s qsize_after_put=%s cycle_s=%s",
        machine_id,
        id(q),
        q.qsize(),
        cycle_s,
    )
    # #endregion
    return {"ok": True, "queued": True, "machine_id": machine_id, "qsize": q.qsize()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    hub: Hub = ws.app.state.ws_hub
    hub.add(ws)
    try:
        snap = ws.app.state.vision.snapshot
        await ws.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)


static = settings.static_dir
if static and Path(static).is_dir():
    app.mount("/assets", StaticFiles(directory=Path(static) / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        fp = Path(static) / full_path
        if fp.is_file():
            return FileResponse(fp)
        return FileResponse(Path(static) / "index.html")
