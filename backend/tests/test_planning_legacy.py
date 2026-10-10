"""`build_plan` without preferences must behave exactly like the pre-refactor engine."""

from __future__ import annotations

from datetime import datetime, timezone

from planning_fixtures import add_open_orders, make_session, seed_factory

from app.db.models import PlanRun, PlanSlot
from app.services.planning.plan_engine import build_plan

# Captured from uretim-planlama @ 3796474 (original plan_engine.build_plan) on this fixture:
# (order_line_id, mold_id, machine_id, qty, reason, duration_s, start_offset_s)
LEGACY_SNAPSHOT = [
    (1, 1, 1, 2500, "Skor=0.97; hız öncelikli; öncelik=1; run_ratio=0.998; cycle_gap=0.05", 13125.0, 0),
    (2, None, None, 0, "Stok yeterli (stok=70, sipariş=50)", None, None),
    (5, None, None, 0, "Ürün eşleşmedi — önce ürün bağlayın", None, None),
    (3, 4, 2, 1200, "Skor=0.99; düzen öncelikli; öncelik=1; run_ratio=0.999; cycle_gap=0.033", 18600.0, 0),
    (4, 1, 1, 1500, "Skor=0.99; düzen öncelikli; öncelik=1; run_ratio=0.998; cycle_gap=0.05", 7875.0, 13125),
]


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def test_build_plan_without_prefs_matches_legacy_snapshot():
    db = make_session()
    now = datetime.now(timezone.utc)
    f = seed_factory(db, now)
    add_open_orders(db, f, now)
    db.commit()

    plan = build_plan(db, created_by="test")
    rows = db.query(PlanSlot).filter_by(plan_run_id=plan.id).order_by(PlanSlot.id).all()
    got = []
    for s in rows:
        dur = off = None
        if s.suggested_start:
            dur = round((s.suggested_end - s.suggested_start).total_seconds(), 1)
            off = round((_aware(s.suggested_start) - now).total_seconds())
        got.append((s.order_line_id, s.mold_id, s.machine_id, s.qty_planned, s.reason, dur, off))
    assert got == LEGACY_SNAPSHOT
    assert plan.status == "active"
    assert plan.prefs_json is None and plan.result_json is None


def test_build_plan_supersedes_previous_live_plan():
    db = make_session()
    now = datetime.now(timezone.utc)
    f = seed_factory(db, now)
    add_open_orders(db, f, now)
    db.commit()
    first = build_plan(db)
    second = build_plan(db)
    db.refresh(first)
    assert first.status == "superseded"
    assert second.status == "active"
    assert db.query(PlanRun).count() == 2
