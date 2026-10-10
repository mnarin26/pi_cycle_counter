"""Geçmiş deneme: Excel inspection, T0/horizon, isolation from live data, no look-ahead."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from planning_excel import orders_xlsx, stock_xlsx
from planning_fixtures import T0, add_open_orders, make_session, seed_factory

from app.db.models import (
    Cycle,
    MachineDowntime,
    MoldRun,
    OrderImport,
    OrderLine,
    PlanRun,
    PlanSlot,
    Product,
    StockMovement,
)
from app.services.planning.backtest import detect_t0, inspect_excels, run_backtest
from app.services.planning.engine import (
    current_molds_at,
    downtime_machine_ids_at,
    machine_downtime_risk,
    mold_machine_stats,
)
from app.services.planning.excel_format import patch_excel_format
from app.services.planning.plan_engine import build_plan
from app.services.planning.plan_state import (
    get_current_plan,
    mark_active_plans_stale,
    supersede_open_plans,
)
from app.services.planning.stock import stock_as_of


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _seeded():
    db = make_session()
    f = seed_factory(db, T0)
    db.commit()
    return db, f


# ---------- Excel inspection ----------


def test_inspect_detects_t0_horizon_and_lines():
    db, _ = _seeded()
    rep = inspect_excels(db, orders_xlsx(), stock_xlsx(), orders_filename="siparis.xlsx")
    # «10.09.26 12:06» is Istanbul time → 09:06 UTC
    assert rep["t0"] == "2026-09-10T09:06:00+00:00"
    assert "A1" in rep["t0_source"]
    # last delivery date used is 21.09.26 → end of that local day
    assert rep["horizon_end"] == "2026-09-21T20:59:59+00:00"
    # weekly-total columns (blank date header) are skipped
    assert rep["line_count"] == 7
    overdue = [ln for ln in rep["lines"] if ln["overdue_at_t0"]]
    assert [(ln["product_name_raw"], ln["qty"]) for ln in overdue] == [("YENI_ORTA_MENTESE_TAPASI", 300)]


def test_inspect_matches_products_by_name_or_code_without_writing():
    db, f = _seeded()
    n_products = db.query(Product).count()
    rep = inspect_excels(db, orders_xlsx(), stock_xlsx())
    by_raw = {p["product_name_raw"]: p for p in rep["products"]}
    # order sheet product column is the description (B); DB names are codes → matched via column A
    assert by_raw["YENI_ORTA_MENTESE_TAPASI"]["product_id"] == f["products"]["A"].id
    assert by_raw["YENI_ORTA_MENTESE_TAPASI"]["match"] == "ad"
    # DB name "6101811700 NEM …" → matched by leading code
    assert by_raw["NEM SENSORLU MENTESE KAP. GR. KK ARCP1"]["product_id"] == f["products"]["C"].id
    assert by_raw["BASTET_LOWER_DOOR_BOT_RIGHT_CAP_BLACK"]["product_id"] is None
    assert rep["matched_count"] == 3 and rep["product_count"] == 4
    assert db.query(Product).count() == n_products


def test_inspect_warns_when_headerless_stock_row_is_skipped():
    db, f = _seeded()
    rep = inspect_excels(db, orders_xlsx(), stock_xlsx())
    # default stock format starts at row 2 → row 1 (115979 = 100) is skipped
    assert any("115979" in w for w in rep["warnings"])
    stock_by_pid = {s["product_id"]: s["qty"] for s in rep["stock_rows"] if s["product_id"]}
    assert stock_by_pid[f["products"]["A"].id] == 500

    patch_excel_format(db, {"stock": {"data_start_row": 1}})
    rep2 = inspect_excels(db, orders_xlsx(), stock_xlsx())
    assert not any("115979" in w for w in rep2["warnings"])
    assert any(s["product_name_raw"] == "115979" for s in rep2["stock_rows"])


def test_detect_t0_returns_none_without_stamp():
    from openpyxl import load_workbook
    import io

    ws = load_workbook(io.BytesIO(orders_xlsx(stamp="Sipariş listesi")), data_only=True).active
    assert detect_t0(ws) == (None, None)


# ---------- isolation ----------


def _live_counts(db):
    return {
        "stock_movements": db.query(StockMovement).count(),
        "order_lines": db.query(OrderLine).count(),
        "order_imports": db.query(OrderImport).count(),
        "mold_runs": db.query(MoldRun).count(),
        "cycles": db.query(Cycle).count(),
        "products": db.query(Product).count(),
    }


def test_backtest_never_touches_live_plan_or_ledgers():
    db = make_session()
    now = datetime.now(timezone.utc)
    f = seed_factory(db, now)
    add_open_orders(db, f, now)
    db.commit()
    live = build_plan(db, created_by="canlı")
    live_slots = [(s.id, s.machine_id, s.qty_planned) for s in db.query(PlanSlot).filter_by(plan_run_id=live.id)]
    before = _live_counts(db)

    bt = run_backtest(db, orders_data=orders_xlsx(), stock_data=stock_xlsx(), created_by="test")

    db.refresh(live)
    assert bt.status == "backtest"
    assert live.status == "active"
    assert get_current_plan(db).id == live.id
    assert _live_counts(db) == before
    assert [(s.id, s.machine_id, s.qty_planned) for s in db.query(PlanSlot).filter_by(plan_run_id=live.id)] == live_slots
    assert db.query(PlanSlot).filter_by(plan_run_id=bt.id).count() > 0
    assert all(s.order_line_id is None for s in db.query(PlanSlot).filter_by(plan_run_id=bt.id))


def test_backtest_excluded_from_stale_supersede_and_current():
    db, _ = _seeded()
    bt = run_backtest(db, orders_data=orders_xlsx(), stock_data=stock_xlsx())
    assert get_current_plan(db) is None
    mark_active_plans_stale(db, "x")
    supersede_open_plans(db)
    db.commit()
    db.refresh(bt)
    assert bt.status == "backtest" and bt.stale_reason is None
    live = build_plan(db)
    db.refresh(bt)
    assert bt.status == "backtest"
    assert get_current_plan(db).id == live.id


def test_backtest_from_existing_import_is_read_only():
    db, f = _seeded()
    imp = OrderImport(filename="eski.xlsx", row_count=1, created_at=T0)
    db.add(imp)
    db.flush()
    pa = f["products"]["A"]
    db.add(OrderLine(import_id=imp.id, product_name_raw=pa.name, product_id=pa.id, qty=900,
                     due_date=T0 + timedelta(days=3), status="shipped"))
    db.commit()
    before = _live_counts(db)
    bt = run_backtest(db, import_id=imp.id)
    assert _live_counts(db) == before
    assert db.query(OrderLine).one().status == "shipped"
    slots = db.query(PlanSlot).filter_by(plan_run_id=bt.id).all()
    assert slots[0].order_line_id is not None
    # ledger stock as of T0 (excel_reset 500 one day earlier) → net need 400
    assert slots[0].qty_planned == 400


def test_backtest_stores_inputs_prefs_and_window():
    db, _ = _seeded()
    bt = run_backtest(db, orders_data=orders_xlsx(), stock_data=stock_xlsx(), prefs={"priority": "due"})
    assert _aware(bt.as_of) == T0
    assert _aware(bt.horizon_end) == datetime(2026, 9, 21, 20, 59, 59, tzinfo=timezone.utc)
    assert '"priority": "due"' in bt.prefs_json
    assert '"stock_source": "excel"' in bt.input_json


# ---------- no look-ahead ----------


def _add_future(db, f):
    """Rows that only exist after T0 — a backtest at T0 must not see them."""
    m1, m2, m3 = f["machines"]
    ka1 = f["molds"]["A1"]
    # a long, very slow K-A1 run on M2 that starts before T0 but ends after it
    db.add(MoldRun(mold_id=ka1.id, machine_id=m2.id, started_at=T0 - timedelta(hours=2),
                   ended_at=T0 + timedelta(hours=10), camera_cycles=100, product_added=400))
    for i in range(50):
        t = T0 + timedelta(minutes=i)
        db.add(Cycle(machine_id=m2.id, mold_id=ka1.id, cycle_time_s=90.0, t_start=t,
                     t_end=t + timedelta(seconds=90), is_counted=True))
    # M1 breaks down after T0
    db.add(MachineDowntime(machine_id=m1.id, start_at=T0 + timedelta(hours=1), end_at=None, note="gelecek"))
    # stock arrives after T0
    db.add(StockMovement(product_id=f["products"]["A"].id, qty=99999, kind="excel_reset",
                         created_at=T0 + timedelta(hours=3)))
    # a mold mounted after T0
    db.add(MoldRun(mold_id=f["molds"]["C"].id, machine_id=m1.id, started_at=T0 + timedelta(hours=5)))
    db.commit()


def _bt_signature(db, bt):
    return [
        (s.line_key, s.mold_id, s.machine_id, s.qty_planned, s.reason,
         _aware(s.suggested_start), _aware(s.suggested_end))
        for s in db.query(PlanSlot).filter_by(plan_run_id=bt.id).order_by(PlanSlot.id)
    ]


def test_backtest_result_ignores_data_after_t0():
    db, f = _seeded()
    imp = OrderImport(filename="liste.xlsx", row_count=2, created_at=T0)
    db.add(imp)
    db.flush()
    pa, pc = f["products"]["A"], f["products"]["C"]
    db.add_all([
        OrderLine(import_id=imp.id, product_name_raw=pa.name, product_id=pa.id, qty=3000, due_date=T0 + timedelta(days=2)),
        OrderLine(import_id=imp.id, product_name_raw=pc.name, product_id=pc.id, qty=800, due_date=T0 + timedelta(days=4)),
    ])
    db.commit()
    before = _bt_signature(db, run_backtest(db, import_id=imp.id, as_of=T0))
    _add_future(db, f)
    after = _bt_signature(db, run_backtest(db, import_id=imp.id, as_of=T0))
    assert before == after
    assert all(start is None or start >= T0 for _, _, _, _, _, start, _ in after)


def test_as_of_helpers_hide_future_rows():
    db, f = _seeded()
    m1, m2, m3 = f["machines"]
    stats_before = mold_machine_stats(db, days=45, as_of=T0)
    risk_before = machine_downtime_risk(db, T0)
    _add_future(db, f)
    assert mold_machine_stats(db, days=45, as_of=T0) == stats_before
    assert machine_downtime_risk(db, T0) == risk_before
    assert m1.id not in downtime_machine_ids_at(db, T0)
    assert m1.id in downtime_machine_ids_at(db, T0 + timedelta(hours=2))
    assert current_molds_at(db, T0).get(m1.id) == f["molds"]["A1"].id
    assert stock_as_of(db, f["products"]["A"].id, T0) == (500, "high")
    assert stock_as_of(db, f["products"]["A"].id, T0 + timedelta(hours=4))[0] == 99999


def test_downtime_open_at_t0_excludes_machine_even_if_closed_later():
    db, f = _seeded()
    m3 = f["machines"][2]
    db.add(MachineDowntime(machine_id=m3.id, start_at=T0 - timedelta(hours=1),
                           end_at=T0 + timedelta(hours=6), note="T0'da arızalı"))
    db.commit()
    assert m3.id in downtime_machine_ids_at(db, T0)
    risk = machine_downtime_risk(db, T0)
    # 4 stops started within 30 days before T0; the open one counts only up to T0
    assert risk[m3.id]["stops_30d"] == 4
    assert risk[m3.id]["hours_30d"] == 16.0


# ---------- real customer files (optional, never committed: public repo) ----------

INBOX = os.environ.get("PLANNING_INBOX_DIR")


@pytest.mark.skipif(not INBOX, reason="PLANNING_INBOX_DIR not set")
def test_real_inbox_excels_parse_with_live_format():
    orders = Path(INBOX) / "sipariş listsi.xlsx"
    stock = Path(INBOX) / "stok.xlsx"
    db = make_session()
    rep = inspect_excels(db, orders.read_bytes(), stock.read_bytes())
    assert rep["t0"] == "2026-09-10T09:06:00+00:00"
    assert rep["horizon_end"].startswith("2026-10-26")
    assert rep["line_count"] > 100
    assert rep["product_count"] == 19
