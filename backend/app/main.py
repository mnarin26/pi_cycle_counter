from __future__ import annotations

import asyncio
import json
import logging
import queue
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.session import SessionLocal, init_db
from app.api.routers import analytics, calibration, cameras, events, machines, molds, settings as settings_router
from app.vision.orchestrator import VisionOrchestrator, drain_cycle_queue_item
from app.ws.hub import Hub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    q: queue.Queue = queue.Queue(maxsize=settings.vision_queue_max)
    app.state.vision_queue = q
    app.state.rolling_cycles = {}
    orch = VisionOrchestrator(q)
    orch.start()
    app.state.vision = orch
    app.state.ws_hub = Hub()

    stop_drain = asyncio.Event()

    async def drain_loop():
        while not stop_drain.is_set():
            try:
                while not stop_drain.is_set():
                    item = q.get_nowait()
                    db = SessionLocal()
                    try:
                        drain_cycle_queue_item(db, item, app.state.rolling_cycles)
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
    yield
    stop_drain.set()
    drain_task.cancel()
    bcast_task.cancel()
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
