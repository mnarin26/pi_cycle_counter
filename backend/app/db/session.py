from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import AppSetting, Base, Camera, Machine

_engine = None
SessionLocal = None
TARGET_CAMERA_COUNT = 32
TARGET_MACHINE_COUNT = 128
DEFAULT_SLOTS_PER_CAMERA = 4


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
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
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


def _dedupe_mold_machine_links(conn) -> int:
    """Merge duplicate mold↔machine rows before unique index enforcement."""
    dupes = conn.execute(
        text(
            """
            SELECT mold_id, machine_id, COUNT(*) AS c
            FROM mold_machines
            GROUP BY mold_id, machine_id
            HAVING c > 1
            """
        )
    ).fetchall()
    removed = 0
    for mold_id, machine_id, _count in dupes:
        rows = conn.execute(
            text(
                """
                SELECT id, cycles_attributed, first_seen_at, last_seen_at
                FROM mold_machines
                WHERE mold_id = :mold_id AND machine_id = :machine_id
                ORDER BY id
                """
            ),
            {"mold_id": mold_id, "machine_id": machine_id},
        ).fetchall()
        if len(rows) <= 1:
            continue
        keep_id = rows[0][0]
        total_cycles = sum(int(r[1] or 0) for r in rows)
        first_seen = min(r[2] for r in rows if r[2] is not None)
        last_seen = max(r[3] for r in rows if r[3] is not None)
        conn.execute(
            text(
                """
                UPDATE mold_machines
                SET cycles_attributed = :cycles,
                    first_seen_at = :first_seen,
                    last_seen_at = :last_seen
                WHERE id = :keep_id
                """
            ),
            {
                "cycles": total_cycles,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "keep_id": keep_id,
            },
        )
        for row_id, _, _, _ in rows[1:]:
            conn.execute(text("DELETE FROM mold_machines WHERE id = :id"), {"id": row_id})
            removed += 1
    return removed


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Lightweight forward migration for existing SQLite databases.
    # Adds new columns without requiring Alembic.
    with engine.begin() as conn:
        machine_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(machines)")).fetchall()}
        if "threshold_offset" not in machine_cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN threshold_offset INTEGER NOT NULL DEFAULT 0"))
        if "line_thickness" not in machine_cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN line_thickness INTEGER NOT NULL DEFAULT 7"))
        if "reflector_len_min" not in machine_cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN reflector_len_min INTEGER"))
        if "reflector_len_max" not in machine_cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN reflector_len_max INTEGER"))
        if "occlusion_grace_ms" not in machine_cols:
            conn.execute(text("ALTER TABLE machines ADD COLUMN occlusion_grace_ms INTEGER NOT NULL DEFAULT 300"))
        cycle_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(cycles)")).fetchall()}
        if "is_counted" not in cycle_cols:
            conn.execute(text("ALTER TABLE cycles ADD COLUMN is_counted INTEGER NOT NULL DEFAULT 1"))
        if "exclude_reason" not in cycle_cols:
            conn.execute(text("ALTER TABLE cycles ADD COLUMN exclude_reason VARCHAR(64)"))
        mold_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(molds)")).fetchall()}
        if "stdev_limit_s" not in mold_cols:
            conn.execute(text("ALTER TABLE molds ADD COLUMN stdev_limit_s REAL"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cycles_machine_counted_tend "
                "ON cycles (machine_id, is_counted, t_end)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_events_machine_created "
                "ON events (machine_id, created_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cycles_counted_tend_mold "
                "ON cycles (is_counted, t_end, mold_id, machine_id)"
            )
        )
        conn.execute(text("ANALYZE cycles"))
        conn.execute(text("ANALYZE events"))
        _dedupe_mold_machine_links(conn)
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_mold_machines_mold_machine "
                "ON mold_machines (mold_id, machine_id)"
            )
        )
    db = SessionLocal()
    try:
        changed = False
        cameras = list(db.query(Camera).order_by(Camera.id))
        if len(cameras) < TARGET_CAMERA_COUNT:
            for i in range(len(cameras) + 1, TARGET_CAMERA_COUNT + 1):
                db.add(Camera(name=f"Camera {i}", rtsp_url="", target_width=640, target_fps=8, enabled=False))
            db.flush()
            cameras = list(db.query(Camera).order_by(Camera.id))
            changed = True

        machine_count = db.query(Machine).count()
        if machine_count < TARGET_MACHINE_COUNT:
            used_slots: dict[int, set[int]] = defaultdict(set)
            for camera_id, slot_index in db.query(Machine.camera_id, Machine.slot_index).all():
                used_slots[int(camera_id)].add(int(slot_index))

            for cam in cameras:
                for slot in range(1, DEFAULT_SLOTS_PER_CAMERA + 1):
                    if machine_count >= TARGET_MACHINE_COUNT:
                        break
                    if slot in used_slots[cam.id]:
                        continue
                    machine_count += 1
                    used_slots[cam.id].add(slot)
                    db.add(
                        Machine(
                            camera_id=cam.id,
                            name=f"Machine {machine_count}",
                            slot_index=slot,
                            roi_polygon="[[0.1,0.4],[0.9,0.4],[0.9,0.6],[0.1,0.6]]",
                            axis_p0="[0.1,0.5]",
                            axis_p1="[0.9,0.5]",
                            enabled=False,
                        )
                    )
                    changed = True
                if machine_count >= TARGET_MACHINE_COUNT:
                    break

            # Fallback: if per-camera default slots are already occupied by custom
            # machines, keep appending with higher slot_index values.
            if machine_count < TARGET_MACHINE_COUNT:
                for cam in cameras:
                    next_slot = (max(used_slots[cam.id]) + 1) if used_slots[cam.id] else 1
                    while machine_count < TARGET_MACHINE_COUNT:
                        machine_count += 1
                        db.add(
                            Machine(
                                camera_id=cam.id,
                                name=f"Machine {machine_count}",
                                slot_index=next_slot,
                                roi_polygon="[[0.1,0.4],[0.9,0.4],[0.9,0.6],[0.1,0.6]]",
                                axis_p0="[0.1,0.5]",
                                axis_p1="[0.9,0.5]",
                                enabled=False,
                            )
                        )
                        next_slot += 1
                        changed = True
                        # Keep distribution balanced while filling remaining count.
                        if next_slot > DEFAULT_SLOTS_PER_CAMERA + 32:
                            break
                    if machine_count >= TARGET_MACHINE_COUNT:
                        break

        if changed:
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
