from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import app.db.session as db_session
from app.db.models import Camera
from app.services.reset_production import wipe_production_history
from app.services.stored_settings import (
    add_operator,
    get_section,
    patch_section,
    remove_operator,
    ssh_connection_string,
    ssh_public_view,
    telegram_public_view,
)
from app.services import system_time
from app.services import wifi_ap
from app.services.camera_time_sync import sync_camera_time


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "admin_static"

app = FastAPI(title="Injection Monitor Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


class TelegramOperatorAdd(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    telegram_user_id: str = Field(..., min_length=1, max_length=32)
    level: int = Field(default=2, ge=1, le=2)


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


@app.post("/api/settings/maintenance/reset-production-data")
def reset_production_data_admin():
    """Same DB wipe as main API, served on this port so the admin UI avoids cross-origin fetch."""
    db = db_session.SessionLocal()
    try:
        stats = wipe_production_history(db)
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
def patch_telegram_settings_admin(body: TelegramSettingsPatch):
    db = db_session.SessionLocal()
    try:
        patch = body.model_dump(exclude_unset=True)
        token = patch.pop("bot_token", None)
        if token is not None:
            token = token.strip()
            if token and token != "__UNCHANGED__":
                patch["bot_token"] = token
        patch_section(db, "telegram", patch)
        return telegram_public_view(get_section(db, "telegram"))
    finally:
        db.close()


@app.post("/api/settings/telegram/operators")
def add_telegram_operator_admin(body: TelegramOperatorAdd):
    db = db_session.SessionLocal()
    try:
        try:
            return add_operator(db, name=body.name, telegram_user_id=body.telegram_user_id, level=body.level)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        db.close()


@app.delete("/api/settings/telegram/operators/{user_id}")
def delete_telegram_operator_admin(user_id: str):
    db = db_session.SessionLocal()
    try:
        return remove_operator(db, user_id)
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
