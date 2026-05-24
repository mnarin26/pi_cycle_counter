"""Resolve mold display names and backfill cycle snapshots."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Cycle, Mold


def resolve_cycle_mold_label(
    db: Session,
    mold_id: int | None,
    snapshot: str | None = None,
) -> str | None:
    """
    Authoritative label for a cycle on charts/dashboards.
    - mold_id null → unassigned (ignore stale snapshot text)
    - mold_id set → live Mold.name; snapshot only if mold row missing
    """
    if mold_id is None:
        return None
    m = db.get(Mold, mold_id)
    if m:
        return m.name or snapshot
    return None


def resolve_mold_display_name(
    db: Session,
    mold_id: int | None,
    snapshot: str | None = None,
) -> str | None:
    return resolve_cycle_mold_label(db, mold_id, snapshot)


def backfill_mold_name_snapshots(db: Session, mold_id: int, name: str) -> int:
    """Update historical cycles when a mold is named or renamed."""
    name = (name or "").strip()
    if not name:
        return 0
    return int(
        db.query(Cycle)
        .filter(Cycle.mold_id == mold_id)
        .update({Cycle.mold_name_snapshot: name}, synchronize_session=False)
        or 0
    )


def clear_mold_name_snapshots(db: Session, mold_id: int) -> int:
    return int(
        db.query(Cycle)
        .filter(Cycle.mold_id == mold_id)
        .update({Cycle.mold_name_snapshot: None}, synchronize_session=False)
        or 0
    )


def clear_orphan_cycle_mold_labels(db: Session, machine_id: int | None = None) -> int:
    """Cycles with no mold_id should not keep old snapshot text."""
    q = db.query(Cycle).filter(Cycle.mold_id.is_(None), Cycle.mold_name_snapshot.is_not(None))
    if machine_id is not None:
        q = q.filter(Cycle.machine_id == machine_id)
    cleared = int(q.update({Cycle.mold_name_snapshot: None}, synchronize_session=False) or 0)

    valid_ids = [int(mid) for (mid,) in db.query(Mold.id).all()]
    dead_q = db.query(Cycle).filter(Cycle.mold_id.is_not(None))
    if machine_id is not None:
        dead_q = dead_q.filter(Cycle.machine_id == machine_id)
    if valid_ids:
        dead_q = dead_q.filter(~Cycle.mold_id.in_(valid_ids))
    cleared += int(
        dead_q.update({Cycle.mold_id: None, Cycle.mold_name_snapshot: None}, synchronize_session=False) or 0
    )
    return cleared


def mold_name_map(db: Session, mold_ids: set[int]) -> dict[int, str | None]:
    if not mold_ids:
        return {}
    rows = db.query(Mold.id, Mold.name).filter(Mold.id.in_(mold_ids)).all()
    return {int(mid): nm for mid, nm in rows}


def enrich_series_mold_names(db: Session, series: list[dict]) -> list[dict]:
    mold_ids = {int(row["mold_id"]) for row in series if row.get("mold_id") is not None}
    names = mold_name_map(db, mold_ids)
    for row in series:
        mid = row.get("mold_id")
        if mid is None:
            row["mold"] = None
        else:
            row["mold"] = names.get(int(mid))
    return series
