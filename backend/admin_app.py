from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db.session import SessionLocal, init_db
from app.db.models import Camera
from app.services.reset_production import wipe_production_history
from app.services import system_time
from app.services.camera_time_sync import sync_camera_time


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "admin_static"

app = FastAPI(title="Injection Monitor Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup_init_db() -> None:
    init_db()


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


@app.post("/api/cameras/{camera_id}/sync-time")
def sync_camera_time_admin(camera_id: int):
    """Push Pi wall clock to IP camera OSD (Hikvision / Dahua / XM-style CGI)."""
    db = SessionLocal()
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
    db = SessionLocal()
    try:
        stats = wipe_production_history(db)
        return {"ok": True, **stats}
    finally:
        db.close()


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    target = STATIC_DIR / full_path
    if target.is_file():
        return FileResponse(target)
    return FileResponse(STATIC_DIR / "index.html")
