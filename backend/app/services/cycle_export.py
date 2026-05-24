"""Build merged CSV exports from SQLite (single download file per request)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Cycle, Machine, Mold
from app.services.cycle_daily_log import CYCLE_CSV_HEADER, istanbul_datetime_str
from app.services.mold_names import mold_name_map, resolve_cycle_mold_label
from app.services.time_windows import to_istanbul


def _csv_string(rows: list[list], delimiter: str = ";") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    for row in rows:
        w.writerow(row)
    return buf.getvalue()


def _period_labels(start: datetime, end: datetime) -> tuple[str, str]:
    s = to_istanbul(start).strftime("%Y-%m-%d %H:%M")
    e = to_istanbul(end).strftime("%Y-%m-%d %H:%M")
    return s, e


def export_filename(prefix: str, start: datetime, end: datetime, kind: str) -> str:
    s = to_istanbul(start).strftime("%Y-%m-%d")
    e = to_istanbul(end).strftime("%Y-%m-%d")
    return f"{prefix}_{s}_{e}_{kind}.csv"


def machine_summary_csv(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
) -> str:
    m = db.get(Machine, machine_id)
    if not m:
        return ""
    filt = (
        Cycle.machine_id == machine_id,
        Cycle.t_end >= start,
        Cycle.t_end <= end,
        Cycle.is_counted.is_(True),
    )
    total = int(db.query(func.count(Cycle.id)).filter(*filt).scalar() or 0)
    agg = db.query(
        func.avg(Cycle.cycle_time_s),
        func.min(Cycle.cycle_time_s),
        func.max(Cycle.cycle_time_s),
    ).filter(*filt).one()
    ps, pe = _period_labels(start, end)
    rows: list[list] = [
        ["kayit", "makine_id", "makine_adi", "baslangic", "bitis", "toplam_dongu", "ort_dongu_s", "min_s", "max_s"],
        [
            "ozet",
            machine_id,
            m.name,
            ps,
            pe,
            total,
            f"{float(agg[0] or 0):.3f}" if total else "0",
            f"{float(agg[1] or 0):.3f}" if total else "0",
            f"{float(agg[2] or 0):.3f}" if total else "0",
        ],
        [],
        ["kalip_adi", "dongu_adedi", "pay_yuzde", "ort_dongu_s"],
    ]
    mold_rows = (
        db.query(
            Cycle.mold_id,
            Cycle.mold_name_snapshot,
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
        )
        .filter(*filt)
        .group_by(Cycle.mold_id, Cycle.mold_name_snapshot)
        .order_by(func.count(Cycle.id).desc())
        .all()
    )
    mold_ids = {int(mid) for mid, _, _, _ in mold_rows if mid is not None}
    names = mold_name_map(db, mold_ids)
    for mid, snap, cnt, avg in mold_rows:
        c = int(cnt or 0)
        label = resolve_cycle_mold_label(db, mid, snap) if mid else (snap or "—")
        if mid and int(mid) in names:
            label = names[int(mid)]
        rows.append(
            [
                label or "—",
                c,
                f"{100.0 * c / total:.2f}" if total else "0",
                f"{float(avg or 0):.3f}",
            ]
        )
    return _csv_string(rows)


def machine_cycles_csv(
    db: Session,
    machine_id: int,
    machine_name: str,
    start: datetime,
    end: datetime,
) -> str:
    rows = (
        db.query(Cycle)
        .filter(
            Cycle.machine_id == machine_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .order_by(Cycle.t_end)
        .all()
    )
    mold_ids = {int(c.mold_id) for c in rows if c.mold_id is not None}
    names = mold_name_map(db, mold_ids)
    out: list[list] = [CYCLE_CSV_HEADER]
    for c in rows:
        mold_label = ""
        if c.mold_id is not None:
            mold_label = names.get(int(c.mold_id)) or c.mold_name_snapshot or f"#{c.mold_id}"
        else:
            mold_label = c.mold_name_snapshot or ""
        out.append(
            [
                istanbul_datetime_str(c.t_end),
                machine_id,
                machine_name,
                "" if c.mold_id is None else int(c.mold_id),
                mold_label,
                f"{float(c.cycle_time_s):.4f}",
                "evet",
                f"{float(c.confidence or 1.0):.4f}",
            ]
        )
    return _csv_string(out)


def molds_summary_csv(
    db: Session,
    start: datetime,
    end: datetime,
    mold_id: int | None = None,
) -> str:
    machine_names = {m.id: m.name for m in db.query(Machine.id, Machine.name).all()}
    q = (
        db.query(
            Cycle.mold_id,
            Cycle.machine_id,
            func.count(Cycle.id),
            func.avg(Cycle.cycle_time_s),
        )
        .filter(
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
            Cycle.mold_id.is_not(None),
        )
        .group_by(Cycle.mold_id, Cycle.machine_id)
    )
    if mold_id is not None:
        q = q.filter(Cycle.mold_id == mold_id)
    agg_rows = q.all()
    molds = {m.id: m for m in db.query(Mold).all()}
    ps, pe = _period_labels(start, end)
    rows: list[list] = [
        [
            "kalip_id",
            "kalip_adi",
            "baslangic",
            "bitis",
            "makine_id",
            "makine_adi",
            "dongu_adedi",
            "ort_dongu_s",
        ],
    ]
    totals: dict[int, int] = {}
    for mid, machine_id, cnt, avg in agg_rows:
        if mid is None:
            continue
        mold = molds.get(int(mid))
        c = int(cnt or 0)
        totals[int(mid)] = totals.get(int(mid), 0) + c
        rows.append(
            [
                int(mid),
                (mold.name if mold else None) or f"#{mid}",
                ps,
                pe,
                int(machine_id),
                machine_names.get(int(machine_id), f"Makine #{machine_id}"),
                c,
                f"{float(avg or 0):.3f}",
            ]
        )
    if mold_id is None and totals:
        rows.append([])
        rows.append(["kalip_id", "kalip_adi", "toplam_dongu"])
        for mid, tot in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
            mold = molds.get(mid)
            rows.append([mid, (mold.name if mold else None) or f"#{mid}", tot])
    return _csv_string(rows)


def mold_cycles_csv(
    db: Session,
    mold_id: int,
    start: datetime,
    end: datetime,
) -> str:
    mold = db.get(Mold, mold_id)
    mold_name = mold.name if mold else f"#{mold_id}"
    machine_names = {m.id: m.name for m in db.query(Machine.id, Machine.name).all()}
    rows = (
        db.query(Cycle)
        .filter(
            Cycle.mold_id == mold_id,
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
        )
        .order_by(Cycle.t_end)
        .all()
    )
    out: list[list] = [CYCLE_CSV_HEADER]
    for c in rows:
        out.append(
            [
                istanbul_datetime_str(c.t_end),
                int(c.machine_id),
                machine_names.get(int(c.machine_id), f"Makine #{c.machine_id}"),
                mold_id,
                mold_name or "",
                f"{float(c.cycle_time_s):.4f}",
                "evet",
                f"{float(c.confidence or 1.0):.4f}",
            ]
        )
    return _csv_string(out)


def molds_all_cycles_csv(db: Session, start: datetime, end: datetime) -> str:
    """All molds — one merged cycles file."""
    machine_names = {m.id: m.name for m in db.query(Machine.id, Machine.name).all()}
    molds = {m.id: m.name for m in db.query(Mold.id, Mold.name).all()}
    rows = (
        db.query(Cycle)
        .filter(
            Cycle.t_end >= start,
            Cycle.t_end <= end,
            Cycle.is_counted.is_(True),
            Cycle.mold_id.is_not(None),
        )
        .order_by(Cycle.t_end)
        .all()
    )
    out: list[list] = [CYCLE_CSV_HEADER]
    for c in rows:
        mid = int(c.mold_id) if c.mold_id is not None else None
        out.append(
            [
                istanbul_datetime_str(c.t_end),
                int(c.machine_id),
                machine_names.get(int(c.machine_id), f"Makine #{c.machine_id}"),
                mid if mid is not None else "",
                molds.get(mid, c.mold_name_snapshot or "") if mid else "",
                f"{float(c.cycle_time_s):.4f}",
                "evet",
                f"{float(c.confidence or 1.0):.4f}",
            ]
        )
    return _csv_string(out)
