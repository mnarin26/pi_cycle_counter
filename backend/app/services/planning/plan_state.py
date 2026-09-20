"""Active / stale plan lifecycle helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PlanRun


def mark_active_plans_stale(db: Session, reason: str) -> int:
    """Mark all active plans as stale (order/stock changed)."""
    n = 0
    for p in db.query(PlanRun).filter(PlanRun.status == "active").all():
        p.status = "stale"
        p.stale_reason = (reason or "Veri değişti")[:256]
        n += 1
    return n


def supersede_open_plans(db: Session) -> None:
    """Mark active/stale plans as superseded before creating a fresh one."""
    for p in db.query(PlanRun).filter(PlanRun.status.in_(("active", "stale"))).all():
        p.status = "superseded"


def get_current_plan(db: Session) -> PlanRun | None:
    """Prefer active, else latest stale (still the 'current' suggestion)."""
    active = (
        db.query(PlanRun)
        .filter(PlanRun.status == "active")
        .order_by(PlanRun.id.desc())
        .first()
    )
    if active:
        return active
    return (
        db.query(PlanRun)
        .filter(PlanRun.status == "stale")
        .order_by(PlanRun.id.desc())
        .first()
    )
