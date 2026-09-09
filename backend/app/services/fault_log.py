"""Diagnosis alarms + minute samples (separate SQLite, vision-thread safe).

Model:
  fault_alarms  — one row per alarm episode (start/resolve)
  fault_alarm_samples — 1/min detail snapshots while alarm is active

Resolve rules are per fault code (caller decides when to resolve).
Never raises to callers.

Hot-path design (vision thread calls note_active() every frame while a fault
condition holds):
  * One persistent module-level connection (check_same_thread=False); every
    access is serialized under `_lock`.
  * Schema is ensured once per process (`_schema_ready`).
  * Active-alarm cache is loaded once per process (`_cache_loaded`) and kept
    in sync by note_active/resolve/delete*; a delete forces one reload.
  * note_active() first consults memory: if an alarm for (machine, code) is
    already open and the 60 s sample interval has not elapsed it returns
    without touching SQLite at all. The DB is only hit when an alarm is
    opened or when a sample is due; the "row still alive" re-check (admin
    deleted it from another process) happens only at that moment.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.fault_codes import (
    FAULT_CATALOG,
    FAULT_RETENTION_DAYS,
    FAULT_SAMPLE_INTERVAL_S,
    get_fault,
    suggest_for_code,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# (machine_id, code) -> last sample mono while that active alarm is open
_last_sample_mono: dict[tuple[int, str], float] = {}
# (machine_id, code) -> active alarm id
_active_alarm_id: dict[tuple[int, str], int] = {}
# True once _active_alarm_id has been (re)built from the DB in this process.
_cache_loaded = False
_writes_since_prune = 0
_PRUNE_EVERY_N = 20

# Shared persistent connection (guarded by _lock).
_conn: sqlite3.Connection | None = None
_schema_ready = False


def _db_path() -> Path:
    d = Path(settings.logs_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / "diagnosis.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=2.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _get_conn_locked() -> sqlite3.Connection:
    """Return the shared connection (open + ensure schema on first use). Caller holds _lock."""
    global _conn, _schema_ready
    if _conn is None:
        _conn = _connect()
        _schema_ready = False
    if not _schema_ready:
        _ensure_schema(_conn)
        _schema_ready = True
    return _conn


def _drop_conn() -> None:
    """Close the shared connection after an error so the next call reconnects."""
    global _conn, _schema_ready, _cache_loaded
    with _lock:
        c = _conn
        _conn = None
        _schema_ready = False
        _cache_loaded = False
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fault_alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            machine_id INTEGER NOT NULL,
            machine_name TEXT,
            code TEXT NOT NULL,
            name TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fault_alarm_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alarm_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            detail_json TEXT,
            FOREIGN KEY(alarm_id) REFERENCES fault_alarms(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_fault_alarms_created ON fault_alarms(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_fault_alarms_active "
        "ON fault_alarms(machine_id, code, resolved_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_fault_samples_alarm "
        "ON fault_alarm_samples(alarm_id, created_at)"
    )
    conn.commit()


def _prune_locked(conn: sqlite3.Connection) -> None:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=FAULT_RETENTION_DAYS)
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "DELETE FROM fault_alarm_samples WHERE alarm_id IN "
        "(SELECT id FROM fault_alarms WHERE created_at < ?)",
        (cutoff,),
    )
    conn.execute("DELETE FROM fault_alarms WHERE created_at < ?", (cutoff,))
    # Also drop orphan samples older than retention by sample time
    conn.execute("DELETE FROM fault_alarm_samples WHERE created_at < ?", (cutoff,))
    conn.commit()


def prune_now() -> int:
    try:
        with _lock:
            conn = _get_conn_locked()
            before = conn.execute("SELECT COUNT(*) FROM fault_alarms").fetchone()[0]
            _prune_locked(conn)
            after = conn.execute("SELECT COUNT(*) FROM fault_alarms").fetchone()[0]
            return int(before) - int(after)
    except Exception:
        logger.exception("fault_log.prune_now failed")
        _drop_conn()
        return 0


def _reload_active_cache_locked(conn: sqlite3.Connection) -> None:
    """Rebuild in-memory active alarm map from DB (process restart safe)."""
    global _cache_loaded
    _active_alarm_id.clear()
    rows = conn.execute(
        "SELECT id, machine_id, code FROM fault_alarms WHERE resolved_at IS NULL"
    ).fetchall()
    for aid, mid, code in rows:
        _active_alarm_id[(int(mid), str(code))] = int(aid)
    _cache_loaded = True


def _ensure_cache_locked(conn: sqlite3.Connection) -> None:
    if not _cache_loaded:
        _reload_active_cache_locked(conn)


def _maybe_prune_locked(conn: sqlite3.Connection) -> None:
    global _writes_since_prune
    _writes_since_prune += 1
    if _writes_since_prune >= _PRUNE_EVERY_N:
        _writes_since_prune = 0
        _prune_locked(conn)


def note_active(
    *,
    machine_id: int,
    machine_name: str,
    code: str,
    detail: dict[str, Any] | None = None,
    now_mono: float | None = None,
) -> bool:
    """While fault condition is true: open alarm if needed, sample ~1/min.

    Returns True if a DB write happened (start and/or sample).
    Memory-first: when the alarm is already open and no sample is due this
    returns False without any SQLite access.
    """
    fault = get_fault(code)
    if not fault:
        return False
    mono = float(now_mono if now_mono is not None else time.monotonic())
    key = (int(machine_id), code)
    try:
        with _lock:
            # --- memory-only fast path (no DB) ---
            alarm_id = _active_alarm_id.get(key)
            if alarm_id is not None:
                last = _last_sample_mono.get(key, 0.0)
                if last > 0 and (mono - last) < FAULT_SAMPLE_INTERVAL_S:
                    return False

            # --- slow path: open alarm or write a due sample ---
            conn = _get_conn_locked()
            if alarm_id is None and not _cache_loaded:
                _reload_active_cache_locked(conn)
                alarm_id = _active_alarm_id.get(key)
                if alarm_id is not None:
                    # Alarm survived a restart: no sample mono known -> sample now.
                    _last_sample_mono.pop(key, None)

            if alarm_id is not None:
                # Sample is due: admin may have deleted the row from another process.
                alive = conn.execute(
                    "SELECT id FROM fault_alarms WHERE id=? AND resolved_at IS NULL",
                    (int(alarm_id),),
                ).fetchone()
                if not alive:
                    _active_alarm_id.pop(key, None)
                    _last_sample_mono.pop(key, None)
                    alarm_id = None

            created = _utc_now_iso()
            if alarm_id is None:
                cur = conn.execute(
                    """
                    INSERT INTO fault_alarms
                    (created_at, resolved_at, machine_id, machine_name, code, name)
                    VALUES (?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        created,
                        int(machine_id),
                        (machine_name or "")[:128],
                        fault["code"],
                        fault["name"],
                    ),
                )
                alarm_id = int(cur.lastrowid)
                _active_alarm_id[key] = alarm_id
            conn.execute(
                """
                INSERT INTO fault_alarm_samples (alarm_id, created_at, detail_json)
                VALUES (?, ?, ?)
                """,
                (
                    alarm_id,
                    created,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )
            _last_sample_mono[key] = mono
            conn.commit()
            _maybe_prune_locked(conn)
            return True
    except Exception:
        logger.exception("fault_log.note_active failed mid=%s code=%s", machine_id, code)
        _drop_conn()
        return False


def resolve(
    *,
    machine_id: int,
    code: str,
) -> bool:
    """Close active alarm for (machine, code) immediately. Returns True if resolved."""
    key = (int(machine_id), code)
    try:
        with _lock:
            conn = _get_conn_locked()
            _ensure_cache_locked(conn)
            alarm_id = _active_alarm_id.get(key)
            if alarm_id is None:
                # DB fallback
                row = conn.execute(
                    """
                    SELECT id FROM fault_alarms
                    WHERE machine_id=? AND code=? AND resolved_at IS NULL
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(machine_id), code),
                ).fetchone()
                if not row:
                    return False
                alarm_id = int(row[0])
            resolved = _utc_now_iso()
            conn.execute(
                "UPDATE fault_alarms SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                (resolved, alarm_id),
            )
            conn.commit()
            _active_alarm_id.pop(key, None)
            _last_sample_mono.pop(key, None)
            return True
    except Exception:
        logger.exception("fault_log.resolve failed mid=%s code=%s", machine_id, code)
        _drop_conn()
        return False


def list_alarms(
    *,
    machine_id: int | None = None,
    code: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """status: active | past | None(all)."""
    limit = max(1, min(int(limit), 1000))
    try:
        with _lock:
            conn = _get_conn_locked()
            _prune_locked(conn)
            sql = (
                "SELECT id, created_at, resolved_at, machine_id, machine_name, code, name "
                "FROM fault_alarms WHERE 1=1"
            )
            args: list[Any] = []
            if machine_id is not None:
                sql += " AND machine_id = ?"
                args.append(int(machine_id))
            if code:
                sql += " AND code = ?"
                args.append(str(code))
            st = (status or "").strip().lower()
            if st == "active":
                sql += " AND resolved_at IS NULL"
            elif st in ("past", "resolved", "passive"):
                sql += " AND resolved_at IS NOT NULL"
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
            rows = conn.execute(sql, args).fetchall()
    except Exception:
        logger.exception("fault_log.list_alarms failed")
        _drop_conn()
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        code_s = r[5]
        meta = FAULT_CATALOG.get(code_s) or {}
        out.append(
            {
                "id": r[0],
                "created_at": r[1],
                "resolved_at": r[2],
                "machine_id": r[3],
                "machine_name": r[4],
                "code": code_s,
                "name": r[6],
                "title_tr": meta.get("title_tr") or r[6],
                "active": r[2] is None,
            }
        )
    return out


def get_alarm(alarm_id: int) -> dict[str, Any] | None:
    """Full alarm + ALL samples for that episode (1/min while active)."""
    try:
        with _lock:
            conn = _get_conn_locked()
            row = conn.execute(
                """
                SELECT id, created_at, resolved_at, machine_id, machine_name, code, name
                FROM fault_alarms WHERE id=?
                """,
                (int(alarm_id),),
            ).fetchone()
            if not row:
                return None
            samples_raw = conn.execute(
                """
                SELECT id, created_at, detail_json
                FROM fault_alarm_samples
                WHERE alarm_id=?
                ORDER BY id ASC
                """,
                (int(alarm_id),),
            ).fetchall()
    except Exception:
        logger.exception("fault_log.get_alarm failed id=%s", alarm_id)
        _drop_conn()
        return None

    code_s = row[5]
    meta = FAULT_CATALOG.get(code_s) or {}
    samples: list[dict[str, Any]] = []
    for s in samples_raw:
        try:
            detail = json.loads(s[2]) if s[2] else {}
        except json.JSONDecodeError:
            detail = {}
        samples.append({"id": s[0], "created_at": s[1], "detail": detail})

    last_detail = samples[-1]["detail"] if samples else {}
    suggestions = suggest_for_code(code_s, last_detail)

    return {
        "id": row[0],
        "created_at": row[1],
        "resolved_at": row[2],
        "machine_id": row[3],
        "machine_name": row[4],
        "code": code_s,
        "name": row[6],
        "title_tr": meta.get("title_tr") or row[6],
        "description_tr": meta.get("description_tr") or "",
        "active": row[2] is None,
        "sample_count": len(samples),
        "samples": samples,
        "suggestions": suggestions,
    }


def delete_alarm(alarm_id: int) -> bool:
    """Delete one alarm + its samples. Clears in-memory active cache if needed."""
    global _cache_loaded
    aid = int(alarm_id)
    try:
        with _lock:
            conn = _get_conn_locked()
            row = conn.execute(
                "SELECT machine_id, code FROM fault_alarms WHERE id=?",
                (aid,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM fault_alarm_samples WHERE alarm_id=?", (aid,)
            )
            conn.execute("DELETE FROM fault_alarms WHERE id=?", (aid,))
            conn.commit()
            key = (int(row[0]), str(row[1]))
            if _active_alarm_id.get(key) == aid:
                _active_alarm_id.pop(key, None)
                _last_sample_mono.pop(key, None)
            # Rows changed under us: rebuild the active map on next DB touch.
            _cache_loaded = False
            return True
    except Exception:
        logger.exception("fault_log.delete_alarm failed id=%s", alarm_id)
        _drop_conn()
        return False


def delete_alarms(
    *,
    machine_id: int | None = None,
    code: str | None = None,
    status: str | None = None,
) -> int:
    """Delete alarms matching filters (same as list). Empty filters = all. Returns count."""
    global _cache_loaded
    try:
        with _lock:
            conn = _get_conn_locked()
            sql = "SELECT id, machine_id, code FROM fault_alarms WHERE 1=1"
            args: list[Any] = []
            if machine_id is not None:
                sql += " AND machine_id = ?"
                args.append(int(machine_id))
            if code:
                sql += " AND code = ?"
                args.append(str(code))
            st = (status or "").strip().lower()
            if st == "active":
                sql += " AND resolved_at IS NULL"
            elif st in ("past", "resolved", "passive"):
                sql += " AND resolved_at IS NOT NULL"
            rows = conn.execute(sql, args).fetchall()
            if not rows:
                return 0
            ids = [int(r[0]) for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"DELETE FROM fault_alarm_samples WHERE alarm_id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM fault_alarms WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
            id_set = set(ids)
            for key, aid in list(_active_alarm_id.items()):
                if aid in id_set:
                    _active_alarm_id.pop(key, None)
                    _last_sample_mono.pop(key, None)
            # Rows changed under us: rebuild the active map on next DB touch.
            _cache_loaded = False
            return len(ids)
    except Exception:
        logger.exception("fault_log.delete_alarms failed")
        _drop_conn()
        return 0


# --- Backward-compatible thin wrappers (old event API) ---

def maybe_log(
    *,
    machine_id: int,
    machine_name: str,
    code: str,
    detail: dict[str, Any] | None = None,
    force: bool = False,
    now_mono: float | None = None,
) -> bool:
    """Deprecated path: prefer note_active / resolve. Maps to note_active."""
    _ = force
    return note_active(
        machine_id=machine_id,
        machine_name=machine_name,
        code=code,
        detail=detail,
        now_mono=now_mono,
    )


def clear_throttle(machine_id: int, code: str | None = None) -> None:
    """Deprecated: use resolve()."""
    if code:
        resolve(machine_id=machine_id, code=code)
    else:
        with _lock:
            codes = [k[1] for k in list(_active_alarm_id.keys()) if k[0] == int(machine_id)]
        for c in codes:
            resolve(machine_id=machine_id, code=c)


def list_events(
    *,
    machine_id: int | None = None,
    code: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Legacy flat sample list (unused by new UI)."""
    alarms = list_alarms(machine_id=machine_id, code=code, limit=limit)
    out: list[dict[str, Any]] = []
    for a in alarms:
        out.append(
            {
                "id": a["id"],
                "created_at": a["created_at"],
                "machine_id": a["machine_id"],
                "machine_name": a["machine_name"],
                "code": a["code"],
                "name": a["name"],
                "title_tr": a["title_tr"],
                "detail": {"resolved_at": a.get("resolved_at"), "active": a.get("active")},
            }
        )
    return out
