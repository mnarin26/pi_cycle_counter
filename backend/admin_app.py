from pathlib import Path
import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import app.db.session as db_session
from app.config import settings as app_settings
from app.db.models import Camera
from app.api.routers.cameras import (
    CameraOut,
    CameraUpdate,
    list_cameras,
    test_camera,
    update_camera,
)
from app.api.routers.machines import MachineOut, MachineUpdate, list_machines, set_roi, update_machine
from app.api.routers import auth as auth_router
from app.api.deps import client_ip, get_db, require_super_or_admin
from app.services import audit_log
from app.services.auth_service import get_session_user, invalidate_operator_sessions
from app.services.reset_production import wipe_production_history
from app.services.stored_settings import (
    add_operator,
    get_section,
    patch_section,
    remove_operator,
    ssh_connection_string,
    ssh_public_view,
    telegram_public_view,
    update_operator,
)
from app.services import system_time
from app.services import wifi_ap
from app.services.camera_time_sync import sync_camera_time
from admin_main_proxy import proxy_main, proxy_main_from_request


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "admin_static"

app = FastAPI(title="Injection Monitor Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_AUTH_ALLOWLIST = {"/api/auth/login", "/api/auth/logout"}


@app.middleware("http")
async def require_session_for_api(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in _AUTH_ALLOWLIST:
        token = request.cookies.get(app_settings.session_cookie_name)
        db = db_session.SessionLocal()
        try:
            user = get_session_user(db, token)
        finally:
            db.close()
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Oturum gerekli"})
        if not (user.is_super or user.has("panel_8080")):
            return JSONResponse(status_code=403, content={"detail": "8080 paneli icin yetkiniz yok"})
    return await call_next(request)


app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])


@app.on_event("startup")
def _startup_init_db() -> None:
    db_session.init_db()


@app.get("/api/system/time")
def get_system_time_admin():
    """Current Pi/system clock for admin UI."""
    return system_time.get_time_status()


class SetSystemTimeBody(BaseModel):
    datetime_local: str = Field(..., description="YYYY-MM-DDTHH:MM or with seconds")
    timezone: str = "Europe/Istanbul"


class SetNtpBody(BaseModel):
    enabled: bool


@app.post("/api/system/time")
def set_system_time_admin(body: SetSystemTimeBody):
    try:
        if body.timezone:
            system_time.set_timezone(body.timezone)
        system_time.set_manual_time(body.datetime_local)
        return {"ok": True, **system_time.get_time_status()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/system/time/ntp")
def set_system_ntp_admin(body: SetNtpBody):
    try:
        system_time.set_ntp_enabled(body.enabled)
        return {"ok": True, **system_time.get_time_status()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/system/wifi-ap")
def get_wifi_ap_admin():
    """Fabrika Wi-Fi AP (hotspot) SSID and status."""
    return wifi_ap.get_wifi_ap_status()


class SetWifiApBody(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: str | None = Field(
        default=None,
        description="Yeni WPA2 sifresi (8-63 karakter). Bos birakilirsa mevcut sifre korunur.",
    )
    reconnect: bool = True


class TelegramSettingsPatch(BaseModel):
    enabled: bool | None = None
    bot_username: str | None = None
    bot_token: str | None = Field(default=None, description="Empty keeps existing token")


class OperatorPermissions(BaseModel):
    panel_8000: bool = False
    panel_8080: bool = False
    bot_mold_create: bool = False
    bot_mold_assign: bool = False


class TelegramOperatorAdd(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    telegram_user_id: str = Field(..., min_length=1, max_length=32)
    role: str = Field(default="user")
    permissions: OperatorPermissions | None = None


class TelegramOperatorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    role: str | None = None
    permissions: OperatorPermissions | None = None


class SshSettingsPatch(BaseModel):
    host: str | None = None
    user: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    auth_method: str | None = None
    key_path: str | None = None
    alias: str | None = None


@app.post("/api/system/wifi-ap")
def set_wifi_ap_admin(body: SetWifiApBody):
    try:
        result = wifi_ap.set_wifi_ap(
            body.ssid,
            body.password,
            reconnect=body.reconnect,
        )
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/cameras/{camera_id}/sync-time")
def sync_camera_time_admin(camera_id: int):
    """Push Pi wall clock to IP camera OSD (Hikvision / Dahua / XM-style CGI)."""
    db = db_session.SessionLocal()
    try:
        cam = db.get(Camera, camera_id)
        if not cam:
            raise HTTPException(status_code=404, detail="Kamera bulunamadi")
        if not (cam.rtsp_url or "").strip():
            raise HTTPException(status_code=400, detail="RTSP URL bos")
        st = system_time.get_time_status()
        tz = st.get("timezone") if isinstance(st.get("timezone"), str) else "Europe/Istanbul"
        if tz in ("unknown", "local") or "/" not in str(tz):
            tz = "Europe/Istanbul"
        result = sync_camera_time(cam.rtsp_url, timezone=tz)
        return {"ok": True, "camera_id": camera_id, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        db.close()


@app.get("/api/cameras", response_model=list[CameraOut])
def admin_list_cameras(request: Request, db=Depends(get_db)):
    return list_cameras(request, db)


@app.patch("/api/cameras/{camera_id}", response_model=CameraOut)
def admin_update_camera(camera_id: int, body: CameraUpdate, db=Depends(get_db)):
    return update_camera(camera_id, body, db)


@app.post("/api/cameras/{camera_id}/test")
def admin_test_camera(camera_id: int, db=Depends(get_db)):
    return test_camera(camera_id, db)


@app.get("/api/cameras/{camera_id}/snapshot.jpg")
async def admin_camera_snapshot(camera_id: int):
    return await proxy_main("GET", f"/api/cameras/{camera_id}/snapshot.jpg")


@app.get("/api/machines", response_model=list[MachineOut])
def admin_list_machines(db=Depends(get_db)):
    return list_machines(db)


@app.patch("/api/machines/{machine_id}", response_model=MachineOut)
def admin_update_machine(machine_id: int, body: MachineUpdate, db=Depends(get_db)):
    return update_machine(machine_id, body, db)


@app.post("/api/machines/{machine_id}/roi")
def admin_set_roi(machine_id: int, roi: list[list[float]], db=Depends(get_db)):
    return set_roi(machine_id, roi, db)


@app.get("/api/live/snapshot")
async def admin_live_snapshot():
    resp = await proxy_main("GET", "/api/live/snapshot")
    return json.loads(resp.body)


@app.websocket("/ws")
async def admin_websocket(ws: WebSocket):
    """Live snapshot stream for admin UI (polls main app on loopback)."""
    await ws.accept()
    try:
        while True:
            try:
                resp = await proxy_main("GET", "/api/live/snapshot")
                data = json.loads(resp.body)
                await ws.send_text(json.dumps({"type": "snapshot", "data": data}))
            except Exception:
                pass
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass


@app.post("/api/calibration/machines/{machine_id}/learn_reflector_length")
async def admin_learn_reflector_length(machine_id: int, request: Request):
    return await proxy_main_from_request(
        request,
        f"/api/calibration/machines/{machine_id}/learn_reflector_length",
        timeout=120.0,
    )


@app.post("/api/settings/maintenance/reset-production-data")
def reset_production_data_admin(request: Request, user=Depends(require_super_or_admin)):
    """Same DB wipe as main API, served on this port so the admin UI avoids cross-origin fetch."""
    db = db_session.SessionLocal()
    try:
        stats = wipe_production_history(db)
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="production.wipe",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            detail=stats,
            ip=client_ip(request),
        )
        return {"ok": True, **stats}
    finally:
        db.close()


@app.get("/api/settings/telegram")
def get_telegram_settings_admin():
    db = db_session.SessionLocal()
    try:
        return telegram_public_view(get_section(db, "telegram"))
    finally:
        db.close()


@app.patch("/api/settings/telegram")
def patch_telegram_settings_admin(body: TelegramSettingsPatch, request: Request, user=Depends(require_super_or_admin)):
    db = db_session.SessionLocal()
    try:
        patch = body.model_dump(exclude_unset=True)
        token = patch.pop("bot_token", None)
        token_changed = False
        if token is not None:
            token = token.strip()
            if token and token != "__UNCHANGED__":
                patch["bot_token"] = token
                token_changed = True
        patch_section(db, "telegram", patch)
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="settings.telegram.update",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            detail={"fields": [k for k in patch if k != "bot_token"], "token_changed": token_changed},
            ip=client_ip(request),
        )
        return telegram_public_view(get_section(db, "telegram"))
    finally:
        db.close()


@app.post("/api/settings/telegram/operators")
def add_telegram_operator_admin(
    body: TelegramOperatorAdd,
    request: Request,
    user=Depends(require_super_or_admin),
):
    role = (body.role or "user").strip().lower()
    if role == "admin" and not user.is_super:
        raise HTTPException(status_code=403, detail="Sadece super kullanici admin tanimlayabilir")
    db = db_session.SessionLocal()
    try:
        try:
            perms = body.permissions.model_dump() if body.permissions else None
            result = add_operator(
                db,
                name=body.name,
                telegram_user_id=body.telegram_user_id,
                role=role,
                permissions=perms,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="operator.create",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            resource=f"operator/{body.telegram_user_id}",
            detail={"name": body.name, "role": role},
            ip=client_ip(request),
        )
        return result
    finally:
        db.close()


@app.patch("/api/settings/telegram/operators/{user_id}")
def update_telegram_operator_admin(
    user_id: str,
    body: TelegramOperatorUpdate,
    request: Request,
    user=Depends(require_super_or_admin),
):
    role = body.role.strip().lower() if body.role else None
    if role == "admin" and not user.is_super:
        raise HTTPException(status_code=403, detail="Sadece super kullanici admin tanimlayabilir")
    db = db_session.SessionLocal()
    try:
        try:
            perms = body.permissions.model_dump() if body.permissions else None
            result = update_operator(
                db,
                telegram_user_id=user_id,
                name=body.name,
                role=role,
                permissions=perms,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        invalidate_operator_sessions(db, user_id)
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="operator.update",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            resource=f"operator/{user_id}",
            detail={"name": body.name, "role": role},
            ip=client_ip(request),
        )
        return result
    finally:
        db.close()


@app.delete("/api/settings/telegram/operators/{user_id}")
def delete_telegram_operator_admin(
    user_id: str,
    request: Request,
    user=Depends(require_super_or_admin),
):
    db = db_session.SessionLocal()
    try:
        result = remove_operator(db, user_id)
        invalidate_operator_sessions(db, user_id)
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="operator.delete",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            resource=f"operator/{user_id}",
            ip=client_ip(request),
        )
        return result
    finally:
        db.close()


@app.get("/api/audit/logs")
def list_audit_logs_admin(limit: int = 100, user=Depends(require_super_or_admin)):
    db = db_session.SessionLocal()
    try:
        return {"logs": audit_log.list_logs(db, limit=limit)}
    finally:
        db.close()


@app.get("/api/settings/ssh")
def get_ssh_settings_admin():
    db = db_session.SessionLocal()
    try:
        raw = get_section(db, "ssh")
        view = ssh_public_view(raw)
        view["connection_string"] = ssh_connection_string(raw)
        return view
    finally:
        db.close()


@app.patch("/api/settings/ssh")
def patch_ssh_settings_admin(body: SshSettingsPatch):
    db = db_session.SessionLocal()
    try:
        patch = body.model_dump(exclude_unset=True)
        if "auth_method" in patch and patch["auth_method"] not in ("key", "password"):
            raise HTTPException(400, detail="auth_method must be key or password")
        raw = patch_section(db, "ssh", patch)
        view = ssh_public_view(raw)
        view["connection_string"] = ssh_connection_string(raw)
        return view
    finally:
        db.close()


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    target = STATIC_DIR / full_path
    if target.is_file():
        return FileResponse(target)
    return FileResponse(STATIC_DIR / "index.html")
