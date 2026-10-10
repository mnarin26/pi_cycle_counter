"""Compare v1.1 (planned vs actual vs risk) and replan suggestions for backtests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from planning_fixtures import T0, make_session, seed_factory

from app.db.models import Cycle, MachineDowntime, MoldRun, OrderImport, OrderLine, PlanRun, PlanSlot, StockMovement
from app.services.planning.backtest import run_backtest
from app.services.planning.compare import compare_backtest
from app.services.planning.replan import suggest_replans


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _actual_run(db, mold, machine, start, hours, cycle_s, cavity):
    end = start + timedelta(hours=hours)
    n = int(hours * 3600 / cycle_s)
    db.add(MoldRun(mold_id=mold.id, machine_id=machine.id, started_at=start, ended_at=end,
                   camera_cycles=n, active_cavity_snapshot=cavity, product_added=n * cavity))
    t = start
    for _ in range(n):
        db.add(Cycle(machine_id=machine.id, mold_id=mold.id, cycle_time_s=cycle_s,
                     t_start=t, t_end=t + timedelta(seconds=cycle_s), is_counted=True))
        t += timedelta(seconds=cycle_s)


def _scenario():
    db = make_session()
    f = seed_factory(db, T0)
    m1, m2, m3 = f["machines"]
    pa, pc = f["products"]["A"], f["products"]["C"]
    imp = OrderImport(filename="liste.xlsx", row_count=2, created_at=T0)
    db.add(imp)
    db.flush()
    db.add_all([
        OrderLine(import_id=imp.id, product_name_raw=pa.name, product_id=pa.id, qty=3000, due_date=T0 + timedelta(days=2)),
        OrderLine(import_id=imp.id, product_name_raw=pc.name, product_id=pc.id, qty=1200, due_date=T0 + timedelta(days=3)),
    ])
    db.commit()
    bt = run_backtest(db, import_id=imp.id, as_of=T0, prefs={})
    # --- what actually happened after T0 ---
    # A made on M3 with the slower K-A2 mold (plan: K-A1 on M1)
    _actual_run(db, f["molds"]["A2"], m3, T0 + timedelta(hours=1), 12, cycle_s=30.0, cavity=2)
    # M2 (planned for C) broke down right at the start
    db.add(MachineDowntime(machine_id=m2.id, start_at=T0 + timedelta(minutes=30),
                           end_at=T0 + timedelta(days=4), note="hidrolik arıza"))
    # C finally made on M1, starting 3.5 days later → misses its due date
    _actual_run(db, f["molds"]["C"], m1, T0 + timedelta(days=3, hours=12), 6, cycle_s=31.0, cavity=2)
    db.commit()
    return db, f, bt


NOW = T0 + timedelta(days=30)


def test_compare_rows_planned_actual_risk_and_reasons():
    db, f, bt = _scenario()
    cmp = compare_backtest(db, bt.id, now=NOW)
    rows = {r["product_id"]: r for r in cmp["rows"]}
    a = rows[f["products"]["A"].id]
    c = rows[f["products"]["C"].id]

    assert a["plan"]["machines"] == ["M1"] and a["actual"]["machines"] == ["M3"]
    assert a["actual"]["status"] == "on_time" and a["actual"]["qty"] == a["need"] == 2500
    assert any("Farklı makinede üretildi (M3)" in r for r in a["reasons"])
    assert any("Çevrim süresi uzadı" in r for r in a["reasons"])

    assert c["actual"]["status"] == "late" and c["risk"]["level"] == "late"
    assert c["actual"]["delay_h"] > 0
    assert any("M2" in r and "duruştaydı" in r and "hidrolik arıza" in r for r in c["reasons"])
    assert any("Üretime geç başlandı" in r for r in c["reasons"])
    assert "gecikme — neden:" in c["summary"]["risk"]
    assert c["summary"]["planned"].startswith("M2, 1.200 adet")


def test_compare_kpis_and_gantt():
    db, f, bt = _scenario()
    cmp = compare_backtest(db, bt.id, now=NOW)
    k = cmp["kpis"]
    assert k["dated_lines"] == 2
    assert k["on_time_plan"] == 1.0 and k["on_time_actual"] == 0.5
    assert k["machine_match"] == 0.0
    assert k["qty_deviation"] == 0.0
    # M3: K-B (mounted at T0) → K-A2, M1: K-A1 → K-C
    assert k["changeovers_actual"] == 2
    names = {r["machine_name"] for r in cmp["gantt"]["rows"]}
    assert {"M1", "M2", "M3"} <= names
    m2 = next(r for r in cmp["gantt"]["rows"] if r["machine_name"] == "M2")
    assert m2["planned"] and m2["downtimes"]
    assert all(_aware(datetime.fromisoformat(b["start"])) >= T0 for r in cmp["gantt"]["rows"] for b in r["actual"])


def test_unknown_reason_when_no_data_explains_delay():
    db = make_session()
    f = seed_factory(db, T0)
    pa = f["products"]["A"]
    imp = OrderImport(filename="x.xlsx", row_count=1, created_at=T0)
    db.add(imp)
    db.flush()
    db.add(OrderLine(import_id=imp.id, product_name_raw=pa.name, product_id=pa.id, qty=1500, due_date=T0 + timedelta(days=1)))
    db.commit()
    bt = run_backtest(db, import_id=imp.id, as_of=T0, prefs={})
    plan_slot = db.query(PlanSlot).filter(PlanSlot.plan_run_id == bt.id, PlanSlot.machine_id.isnot(None)).one()
    m = next(x for x in f["machines"] if x.id == plan_slot.machine_id)
    # produced on the planned machine, same cycle, started on time … but only finished after a pause
    _actual_run(db, f["molds"]["A1"], m, T0, 0.5, cycle_s=21.0, cavity=4)
    _actual_run(db, f["molds"]["A1"], m, T0 + timedelta(days=3), 1, cycle_s=21.0, cavity=4)
    db.commit()
    row = compare_backtest(db, bt.id, now=NOW)["rows"][0]
    assert row["actual"]["status"] == "late"
    assert row["reasons"] == ["Neden bilinmiyor (kayıtlarda açıklayan veri yok)"]


def test_compare_and_replan_are_read_only():
    db, f, bt = _scenario()
    tables = (MoldRun, Cycle, StockMovement, OrderLine, PlanRun, PlanSlot, MachineDowntime)
    before = {t.__tablename__: db.query(t).count() for t in tables}
    cmp = compare_backtest(db, bt.id, now=NOW)
    suggest_replans(db, bt, cmp)
    db.commit()
    assert {t.__tablename__: db.query(t).count() for t in tables} == before
    db.refresh(bt)
    assert bt.status == "backtest"


def test_replan_suggests_alternative_after_downtime():
    db, f, bt = _scenario()
    cmp = compare_backtest(db, bt.id, now=NOW)
    sug = suggest_replans(db, bt, cmp)
    c = next(s for s in sug if s["product_label"].startswith("6101811700"))
    at = datetime.fromisoformat(c["at"])
    assert T0 <= at <= T0 + timedelta(hours=1)
    assert "duruşa geçti" in c["trigger"]
    assert c["machine_name"] == "M1"
    assert c["meets_due"] is True
    assert datetime.fromisoformat(c["finish"]) > at
    assert "yetişirdi" in c["text"] and "önce biterdi" in c["text"]
