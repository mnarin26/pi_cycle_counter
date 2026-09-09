"""Purge SQLite rows and daily CSV files older than retention window."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Cycle, Event
from app.services.anomaly_window import purge_anomaly_older_than
from app.services.cycle_capture import purge_diag_older_than
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
        from app.config import settings as app_settings

        diag_days = int(getattr(app_settings, "diag_retention_days", 14) or 14)
        anomaly_days = int(getattr(app_settings, "anomaly_retention_days", 14) or 14)
    except Exception:
        diag_days = 14
        anomaly_days = 14
    deleted_diag = purge_diag_older_than(logs_dir, diag_days)
    deleted_anomaly = purge_anomaly_older_than(logs_dir, anomaly_days)
    try:
        db.execute(text("VACUUM"))
        db.commit()
    except Exception as e:
        logger.warning("VACUUM skipped: %s", e)
    return {
        "deleted_cycles": int(deleted_cycles or 0),
        "deleted_events": int(deleted_events or 0),
        "deleted_csv_files": deleted_csv,
        "deleted_diag_days": deleted_diag,
        "deleted_anomaly_days": deleted_anomaly,
    }
