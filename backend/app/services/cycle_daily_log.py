"""Daily cycle CSV archive (Europe/Istanbul calendar days)."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from app.db.models import Cycle
from app.services.time_windows import DISPLAY_TZ, ensure_utc, to_istanbul

RETENTION_DAYS = 730

CYCLE_CSV_HEADER = [
    "tarih_saat_tr",
    "makine_id",
    "makine_adi",
    "kalip_id",
    "kalip_adi",
    "dongu_suresi_s",
    "sayildi",
    "guven",
]

_locks: dict[str, Lock] = {}


def _lock_for(path: Path) -> Lock:
    key = str(path.resolve())
    if key not in _locks:
        _locks[key] = Lock()
    return _locks[key]


def istanbul_date(dt: datetime) -> str:
    return to_istanbul(dt).strftime("%Y-%m-%d")


def istanbul_datetime_str(dt: datetime) -> str:
    return to_istanbul(dt).strftime("%Y-%m-%d %H:%M:%S")


def daily_csv_path(logs_dir: Path, machine_id: int, local_date: str) -> Path:
    return logs_dir / f"machine_{machine_id}" / f"{local_date}.csv"


def cycle_to_row(machine_name: str, cycle: Cycle) -> list[str | int | float]:
    return [
        istanbul_datetime_str(cycle.t_end),
        int(cycle.machine_id),
        machine_name,
        "" if cycle.mold_id is None else int(cycle.mold_id),
        cycle.mold_name_snapshot or "",
        f"{float(cycle.cycle_time_s):.4f}",
        "evet" if cycle.is_counted else "hayir",
        f"{float(cycle.confidence or 1.0):.4f}",
    ]


def append_cycle_to_daily_csv(logs_dir: Path, machine_name: str, cycle: Cycle) -> None:
    """Append one counted cycle row to the Istanbul calendar-day file."""
    if not cycle.is_counted:
        return
    local_date = istanbul_date(cycle.t_end)
    fp = daily_csv_path(logs_dir, int(cycle.machine_id), local_date)
    fp.parent.mkdir(parents=True, exist_ok=True)
    lk = _lock_for(fp)
    with lk:
        new_file = not fp.exists()
        with fp.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            if new_file:
                w.writerow(CYCLE_CSV_HEADER)
            w.writerow(cycle_to_row(machine_name, cycle))


def iter_local_dates_in_range(start: datetime, end: datetime) -> list[str]:
    """Inclusive Istanbul dates between start and end."""
    cur = to_istanbul(start).date()
    last = to_istanbul(end).date()
    out: list[str] = []
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def merge_daily_csv_files(
    logs_dir: Path,
    machine_id: int,
    start: datetime,
    end: datetime,
) -> str:
    """Merge on-disk daily CSVs into one CSV string (header once). Used as fallback."""
    lines: list[str] = []
    header_written = False
    for day in iter_local_dates_in_range(start, end):
        fp = daily_csv_path(logs_dir, machine_id, day)
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8")
        rows = text.splitlines()
        if not rows:
            continue
        if not header_written:
            lines.append(rows[0])
            header_written = True
            data_rows = rows[1:]
        else:
            data_rows = rows[1:] if rows[0].startswith("tarih_saat_tr") else rows
        lines.extend(data_rows)
    if not header_written:
        import io

        buf = io.StringIO()
        cw = csv.writer(buf, delimiter=";")
        cw.writerow(CYCLE_CSV_HEADER)
        return buf.getvalue()
    return "\n".join(lines) + "\n"


def purge_daily_csv_older_than(logs_dir: Path, cutoff: datetime) -> int:
    """Delete daily CSV files with Istanbul date strictly before cutoff (UTC instant)."""
    cutoff_local = ensure_utc(cutoff).astimezone(DISPLAY_TZ).date()
    deleted = 0
    if not logs_dir.is_dir():
        return 0
    for machine_dir in logs_dir.glob("machine_*"):
        if not machine_dir.is_dir():
            continue
        for fp in machine_dir.glob("*.csv"):
            try:
                file_date = date.fromisoformat(fp.stem)
            except ValueError:
                continue
            if file_date < cutoff_local:
                fp.unlink(missing_ok=True)
                deleted += 1
    return deleted
