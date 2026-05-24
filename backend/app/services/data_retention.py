"""Purge SQLite rows and daily CSV files older than retention window."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Cycle, Event
from app.services.cycle_daily_log import RETENTION_DAYS, purge_daily_csv_older_than

logger = logging.getLogger(__name__)


def run_data_retention(
    db: Session,
    logs_dir: Path,
    retention_days: int = RETENTION_DAYS,
) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted_cycles = (
        db.query(Cycle).filter(Cycle.t_end < cutoff).delete(synchronize_session=False)
    )
    deleted_events = (
        db.query(Event).filter(Event.created_at < cutoff).delete(synchronize_session=False)
    )
    db.commit()
    deleted_csv = purge_daily_csv_older_than(logs_dir, cutoff)
    try:
        db.execute(text("VACUUM"))
        db.commit()
    except Exception as e:
        logger.warning("VACUUM skipped: %s", e)
    return {
        "deleted_cycles": int(deleted_cycles or 0),
        "deleted_events": int(deleted_events or 0),
        "deleted_csv_files": deleted_csv,
    }
