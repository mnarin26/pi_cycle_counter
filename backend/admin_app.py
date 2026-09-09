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
from app.services import fault_log
from app.services.fault_codes import (
    FAULT_RETENTION_DAYS,
    FAULT_SAMPLE_INTERVAL_S,
    list_faults,
)
from app.services.auth_service import get_session_user, invalidate_operator_sessions
from app.services.reset_production import wipe_production_history
from app.services.production_settings import get_production_settings, patch_production_settings
from app.services.vision_settings import get_vision_settings, patch_vision_settings
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

_AUTH_ALLOWLIST = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/login-mode",
    "/api/debug/agent-log",
}


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
    telegram_user_id: str | None = Field(default="", max_length=32)
    role: str = Field(default="user")
    permissions: OperatorPermissions | None = None
    password: str | None = Field(default=None, max_length=64)


class TelegramOperatorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    role: str | None = None
    permissions: OperatorPermissions | None = None
    password: str | None = Field(default=None, max_length=64)
    telegram_user_id: str | None = Field(default=None, max_length=32)


class SshSettingsPatch(BaseModel):
    host: str | None = None
    user: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    auth_method: str | None = None
    key_path: str | None = None
    alias: str | None = None


class ProductionBreak(BaseModel):
    start: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end: str = Field(..., pattern=r"^(\d{2}:\d{2}|24:00)$")


class ProductionShift(BaseModel):
    id: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    start: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end: str = Field(..., pattern=r"^(\d{2}:\d{2}|24:00)$")
    breaks: list[ProductionBreak] | None = None


class ProductionSettingsPatch(BaseModel):
    tv_rotate_seconds: int | None = Field(default=None, ge=5, le=300)
    idle_stopped_seconds: int | None = Field(default=None, ge=30, le=3600)
    shifts: list[ProductionShift] | None = None


class VisionSettingsPatch(BaseModel):
    sample_interval_ms: int | None = Field(default=None, ge=50, le=500)
    schmitt_min_cycle_s: float | None = Field(default=None, ge=3.0, le=120.0)


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


@app.get("/api/machines/{machine_id}/diag")
def admin_diag_status(machine_id: int, db=Depends(get_db)):
    """Timed process-log window status + disk usage for one machine."""
    from datetime import datetime, timezone
    from pathlib import Path

    from app.db.models import Machine
    from app.services.host_metrics import sample_host
    from app.services.process_log import parse_diag_dt

    def _dir_size(p: Path) -> int:
        if not p.exists():
            return 0
        total = 0
        for f in p.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def _fmt(n: int) -> str:
        x = float(n)
        for u in ("B", "KB", "MB", "GB"):
            if x < 1024 or u == "GB":
                return f"{x:.1f} {u}" if u != "B" else f"{int(x)} B"
            x /= 1024
        return f"{n} B"

    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404, detail="machine not found")
    start = parse_diag_dt(getattr(m, "diag_from", None))
    until = parse_diag_dt(getattr(m, "diag_until", None))
    now = datetime.now(timezone.utc)
    window_ok = until is not None and until > now and (start is None or start < until)
    pending = bool(window_ok and start is not None and now < start)
    active = bool(window_ok and not pending)
    root = Path(app_settings.logs_dir) / "diag" / f"machine_{machine_id}"
    on_disk = _dir_size(root)
    # Light process.csv ~ few MB/day; keep estimate tiny for UI hint.
    hours_left = 0.0
    if active and until is not None:
        hours_left = max(0.0, (until - now).total_seconds() / 3600.0)
    elif pending and until is not None and start is not None:
        hours_left = max(0.0, (until - start).total_seconds() / 3600.0)
    est = int(hours_left * 0.15 * 1024 * 1024)  # ~0.15 MB/h rough
    # First CPU sample may be None (needs /proc/stat delta).
    host = sample_host(force=True).as_dict()
    if host.get("cpu_pct") is None:
        import time as _time

        _time.sleep(0.15)
        host = sample_host(force=True).as_dict()
    return {
        "machine_id": machine_id,
        "diag_from": getattr(m, "diag_from", None),
        "diag_until": getattr(m, "diag_until", None),
        "active": active,
        "pending": pending,
        "bytes_on_disk": on_disk,
        "bytes_on_disk_label": _fmt(on_disk),
        "estimate_remaining_bytes": est,
        "estimate_remaining_label": _fmt(est),
        "estimate_note": "Hafif process.csv (goruntu yok); ~0.15 MB/saat kabaca.",
        "host": host,
    }


@app.get("/api/machines/{machine_id}/diag/estimate")
def admin_diag_estimate(machine_id: int, until: str):
    """Estimate samples growth from now until the given datetime (preview before save)."""
    from datetime import datetime, timezone

    from app.services.cycle_capture import (
        SAMPLES_MB_PER_DAY,
        estimate_samples_bytes,
        format_bytes,
        parse_diag_until,
    )

    dt = parse_diag_until(until)
    if dt is None:
        raise HTTPException(400, detail="gecersiz tarih")
    now = datetime.now(timezone.utc)
    est = estimate_samples_bytes(dt, now=now)
    hours = max(0.0, (dt - now).total_seconds() / 3600.0)
    return {
        "machine_id": machine_id,
        "until": dt.isoformat(),
        "hours": round(hours, 1),
        "estimate_bytes": est,
        "estimate_label": format_bytes(est),
        "samples_mb_per_day": SAMPLES_MB_PER_DAY,
        "note": (
            f"~{SAMPLES_MB_PER_DAY:.0f} MB/gun samples.csv (goruntu yok)"
        ),
    }


@app.get("/api/machines/{machine_id}/diag/download")
def admin_diag_download(machine_id: int, db=Depends(get_db)):
    """Zip and download whatever diag files exist so far for this machine."""
    import tempfile
    import zipfile
    from datetime import datetime

    from app.db.models import Machine
    from app.services.cycle_capture import machine_diag_dir
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404, detail="machine not found")
    root = machine_diag_dir(app_settings.logs_dir, machine_id)
    if not root.is_dir() or not any(root.rglob("*")):
        raise HTTPException(404, detail="Bu makine icin henuz log yok")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in (m.name or f"m{machine_id}")
    )
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"diag_{machine_id}_", suffix=".zip", delete=False
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    def _cleanup(p: Path = tmp_path) -> None:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if not fp.is_file():
                    continue
                zf.write(fp, arcname=fp.relative_to(root).as_posix())
    except Exception:
        _cleanup()
        raise
    filename = f"diag_{safe_name}_{machine_id}_{stamp}.zip"
    return FileResponse(
        path=str(tmp_path),
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_cleanup),
    )


@app.post("/api/machines/{machine_id}/roi")
def admin_set_roi(machine_id: int, roi: list[list[float]], db=Depends(get_db)):
    return set_roi(machine_id, roi, db)


@app.get("/api/live/snapshot")
async def admin_live_snapshot():
    resp = await proxy_main("GET", "/api/live/snapshot")
    return json.loads(resp.body)


@app.post("/api/debug/agent-log")
async def admin_debug_agent_log(request: Request):
    # #region agent log
    try:
        payload = await request.json()
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with open("/tmp/debug-3a2fad.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        return {"ok": False}
    return {"ok": True}
    # #endregion


@app.websocket("/ws")
async def admin_websocket(ws: WebSocket):
    """Bridge admin UI to main app WS (avoid HTTP poll blocking on GIL/JPEG)."""
    await ws.accept()
    import websockets

    upstream = None
    try:
        upstream = await websockets.connect(
            "ws://127.0.0.1:8000/ws",
            open_timeout=5,
            close_timeout=2,
            ping_interval=20,
            ping_timeout=20,
        )

        async def forward_up_to_client() -> None:
            async for msg in upstream:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
                else:
                    await ws.send_text(msg)

        async def keep_main_alive() -> None:
            # Main /ws waits on receive_text; send periodic pings so it stays open.
            while True:
                await asyncio.sleep(25)
                await upstream.send("ping")

        await asyncio.gather(forward_up_to_client(), keep_main_alive())
    except WebSocketDisconnect:
        pass
    except Exception:
        # Fallback: short-timeout HTTP poll if WS bridge fails
        try:
            while True:
                try:
                    resp = await proxy_main("GET", "/api/live/snapshot", timeout=1.0)
                    data = json.loads(resp.body)
                    await ws.send_text(json.dumps({"type": "snapshot", "data": data}))
                except Exception:
                    pass
                await asyncio.sleep(0.35)
        except WebSocketDisconnect:
            pass
    finally:
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:
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
                password=body.password,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="operator.create",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            resource=f"operator/{body.telegram_user_id or body.name}",
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
                password=body.password,
                new_telegram_user_id=body.telegram_user_id,
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


@app.get("/api/diagnosis/codes")
def diagnosis_codes_admin(user=Depends(require_super_or_admin)):
    return {
        "codes": list_faults(),
        "sample_interval_s": FAULT_SAMPLE_INTERVAL_S,
        "retention_days": FAULT_RETENTION_DAYS,
    }


@app.get("/api/diagnosis/alarms")
def diagnosis_alarms_admin(
    machine_id: int | None = None,
    code: str | None = None,
    status: str | None = None,
    limit: int = 200,
    user=Depends(require_super_or_admin),
):
    """Master list: active/past alarms (no sample columns)."""
    fault_log.prune_now()
    return {
        "alarms": fault_log.list_alarms(
            machine_id=machine_id, code=code, status=status, limit=limit
        ),
        "sample_interval_s": FAULT_SAMPLE_INTERVAL_S,
        "retention_days": FAULT_RETENTION_DAYS,
    }


@app.get("/api/diagnosis/alarms/{alarm_id}")
def diagnosis_alarm_detail_admin(alarm_id: int, user=Depends(require_super_or_admin)):
    """Detail: all 1/min samples for the episode + suggestions."""
    row = fault_log.get_alarm(alarm_id)
    if not row:
        raise HTTPException(404, detail="Alarm bulunamadı")
    return row


@app.get("/api/diagnosis/events")
def diagnosis_events_admin(
    machine_id: int | None = None,
    code: str | None = None,
    limit: int = 200,
    user=Depends(require_super_or_admin),
):
    """Legacy flat list (kept for compatibility). Prefer /alarms."""
    fault_log.prune_now()
    return {
        "events": fault_log.list_events(machine_id=machine_id, code=code, limit=limit),
        "sample_interval_s": FAULT_SAMPLE_INTERVAL_S,
        "retention_days": FAULT_RETENTION_DAYS,
    }


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


@app.get("/api/settings/production")
def get_production_settings_admin():
    db = db_session.SessionLocal()
    try:
        return get_production_settings(db)
    finally:
        db.close()


@app.patch("/api/settings/production")
def patch_production_settings_admin(
    body: ProductionSettingsPatch,
    request: Request,
    user=Depends(require_super_or_admin),
):
    db = db_session.SessionLocal()
    try:
        patch = body.model_dump(exclude_unset=True)
        if "shifts" in patch and patch["shifts"] is not None:
            patch["shifts"] = [s.model_dump() for s in body.shifts or []]
        try:
            result = patch_production_settings(db, patch)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="settings.production.update",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            detail={"fields": list(patch.keys())},
            ip=client_ip(request),
        )
        return result
    finally:
        db.close()


@app.get("/api/settings/vision")
def get_vision_settings_admin():
    db = db_session.SessionLocal()
    try:
        return get_vision_settings(db)
    finally:
        db.close()


@app.patch("/api/settings/vision")
def patch_vision_settings_admin(
    body: VisionSettingsPatch,
    request: Request,
    user=Depends(require_super_or_admin),
):
    db = db_session.SessionLocal()
    try:
        patch = body.model_dump(exclude_unset=True)
        try:
            result = patch_vision_settings(db, patch)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="settings.vision.update",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            detail={"fields": list(patch.keys())},
            ip=client_ip(request),
        )
        return result
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
