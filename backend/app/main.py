from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
import app.db.session as db_session
from app.services.auth_service import get_session_user
from app.api.routers import activity, analytics, auth, calibration, cameras, events, machines, molds, settings as settings_router
from app.vision.orchestrator import VisionOrchestrator, drain_cycle_queue_item
from app.ws.hub import Hub
from app.ws.pos_tick_hub import PosTickHub
from app.services.anomaly_window import AnomalyWindowRecorder

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
    pos_tick_hub = PosTickHub()
    app.state.pos_tick_hub = pos_tick_hub
    anomaly = AnomalyWindowRecorder(settings.logs_dir)
    app.state.anomaly = anomaly
    orch = VisionOrchestrator(q, pos_tick_hub=pos_tick_hub, anomaly=anomaly)
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

    async def start_vision_after_bind() -> None:
        """Defer RTSP/vision thread until after uvicorn binds (avoids startup hang)."""
        await asyncio.sleep(0.05)
        if not orch.is_alive():
            orch.start()

    vision_boot = asyncio.create_task(start_vision_after_bind())

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
            snap = _enrich_live_snapshot(app.state.vision.snapshot, app.state.ws_hub)
            msg = json.dumps({"type": "snapshot", "data": snap})
            await app.state.ws_hub.broadcast(msg)
            await asyncio.sleep(interval)

    drain_task = asyncio.create_task(drain_loop())
    bcast_task = asyncio.create_task(broadcast_loop())
    pos_tick_task = asyncio.create_task(pos_tick_hub.pump_forever())

    async def retention_loop():
        from app.services.data_retention import run_data_retention

        # Never block lifespan startup — retention can scan a large DB synchronously.
        await asyncio.sleep(30)
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
    vision_boot.cancel()
    drain_task.cancel()
    bcast_task.cancel()
    pos_tick_task.cancel()
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

# Endpoints reachable without a session. The TV wall (/tv) is public and reads live data.
_AUTH_ALLOWLIST = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/login-mode",
    "/api/health",
    "/api/live/snapshot",
}


ADMIN_PROXY_HEADER = "X-Injection-Admin-Proxy"
ADMIN_PROXY_TOKEN = "loopback-proxy"


def _is_public_api(path: str) -> bool:
    if path in _AUTH_ALLOWLIST:
        return True
    if path.startswith("/api/analytics/tv_board"):
        return True
    if path.startswith("/api/analytics/tv_machine"):
        return True
    if path == "/api/settings/production":
        return True
    return False


def _is_admin_proxy(request: Request) -> bool:
    """8080 admin panel proxy from same Pi (loopback + shared secret)."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return False
    return request.headers.get(ADMIN_PROXY_HEADER) == ADMIN_PROXY_TOKEN


@app.middleware("http")
async def require_session_for_api(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not _is_public_api(path):
        if _is_admin_proxy(request):
            return await call_next(request)
        token = request.cookies.get(settings.session_cookie_name)
        db = db_session.SessionLocal()
        try:
            user = get_session_user(db, token)
        finally:
            db.close()
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Oturum gerekli"})
        if not (user.is_super or user.has("panel_8000")):
            return JSONResponse(status_code=403, content={"detail": "8000 paneli icin yetkiniz yok"})
    return await call_next(request)


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(cameras.router, prefix="/api/cameras", tags=["cameras"])
app.include_router(machines.router, prefix="/api/machines", tags=["machines"])
app.include_router(molds.router, prefix="/api/molds", tags=["molds"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(activity.router, prefix="/api/activity", tags=["activity"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(calibration.router, prefix="/api/calibration", tags=["calibration"])


@app.get("/api/health")
def health():
    return {"ok": True}


def _load_warn(ws_clients: int, cpu_proxy: float) -> dict | None:
    """Load banner disabled — cpu_proxy flicker kept shifting the UI."""
    _ = (ws_clients, cpu_proxy)
    return None


def _enrich_live_snapshot(snap: dict, hub: Hub) -> dict:
    out = dict(snap or {})
    n = int(getattr(hub, "client_count", 0) or 0)
    cpu = float(out.get("cpu_proxy") or 0.0)
    out["ws_clients"] = n
    warn = _load_warn(n, cpu)
    if warn is None:
        out.pop("load_warn", None)
    else:
        out["load_warn"] = warn
    return out


@app.get("/api/live/snapshot")
def live_snapshot():
    """Current vision snapshot (same payload as WebSocket)."""
    return _enrich_live_snapshot(app.state.vision.snapshot, app.state.ws_hub)


@app.post("/api/debug/agent-log")
async def debug_agent_log(request: Request):
    """Temporary debug ingest (NDJSON) for agent sessions — no auth, local only."""
    # #region agent log
    try:
        payload = await request.json()
        line = json.dumps(payload, ensure_ascii=False, default=str)
        path = Path("/tmp/debug-3a2fad.log")
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("debug agent-log failed: %s", e)
        return {"ok": False}
    return {"ok": True}
    # #endregion


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
        snap = _enrich_live_snapshot(ws.app.state.vision.snapshot, hub)
        await ws.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)


@app.websocket("/ws/pos-ticks")
async def websocket_pos_ticks(ws: WebSocket, machine_id: int | None = Query(None)):
    """Shadow stream: per-processed-line pos ticks (no images). Idle = no emit on Pi."""
    await ws.accept()
    hub: PosTickHub = ws.app.state.pos_tick_hub
    filt: set[int] | None = None
    if machine_id is not None and int(machine_id) > 0:
        filt = {int(machine_id)}
    hub.add(ws, filt)
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "machine_id": machine_id,
                    "msg": "pos-ticks subscribed",
                }
            )
        )
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
