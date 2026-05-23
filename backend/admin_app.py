from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db.session import SessionLocal, init_db
from app.services.reset_production import wipe_production_history


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "admin_static"

app = FastAPI(title="Injection Monitor Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup_init_db() -> None:
    init_db()


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
