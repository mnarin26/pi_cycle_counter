from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import AppSetting, Base, Camera, Machine

_engine = None
SessionLocal = None


def get_engine():
    global _engine, SessionLocal
    if _engine is None:
        path = settings.database_url.replace("sqlite:///", "", 1)
        if not path.startswith("sqlite:////"):
            db_path = Path(path)
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path.as_posix()}"
        else:
            url = settings.database_url
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_db() -> Generator[Session, None, None]:
    get_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Lightweight forward migration for existing SQLite databases.
    # Adds new columns without requiring Alembic.
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(machines)")).fetchall()}
        if "threshold_offset" not in cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN threshold_offset INTEGER NOT NULL DEFAULT 0"))
        if "line_thickness" not in cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN line_thickness INTEGER NOT NULL DEFAULT 7"))
        if "reflector_len_min" not in cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN reflector_len_min INTEGER"))
        if "reflector_len_max" not in cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN reflector_len_max INTEGER"))
    db = SessionLocal()
    try:
        if db.query(Camera).count() == 0:
            c1 = Camera(name="Camera 1", rtsp_url="", target_width=640, target_fps=8)
            c2 = Camera(name="Camera 2", rtsp_url="", target_width=640, target_fps=8)
            db.add_all([c1, c2])
            db.flush()
            for i in range(4):
                db.add(
                    Machine(
                        camera_id=c1.id,
                        name=f"Machine {i + 1}",
                        slot_index=i + 1,
                        roi_polygon="[[0.1,0.4],[0.9,0.4],[0.9,0.6],[0.1,0.6]]",
                        axis_p0="[0.1,0.5]",
                        axis_p1="[0.9,0.5]",
                        enabled=False,
                    )
                )
            for i in range(4):
                db.add(
                    Machine(
                        camera_id=c2.id,
                        name=f"Machine {i + 5}",
                        slot_index=i + 1,
                        roi_polygon="[[0.1,0.4],[0.9,0.4],[0.9,0.6],[0.1,0.6]]",
                        axis_p0="[0.1,0.5]",
                        axis_p1="[0.9,0.5]",
                        enabled=False,
                    )
                )
            db.commit()
    finally:
        db.close()
    db2 = SessionLocal()
    try:
        if db2.get(AppSetting, "global") is None:
            db2.add(AppSetting(key="global", value_json="{}"))
            db2.commit()
    finally:
        db2.close()
