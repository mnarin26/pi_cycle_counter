"""A2 engine: stock allocation, split, grouping, changeover, calendar rules, risk, prefs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from planning_fixtures import T0, add_open_orders, make_session, seed_factory

from app.db.models import MoldMachineCapability
from app.services.planning.plan_a2 import compute_plan_a2
from app.services.planning.plan_engine import LineIn, build_plan, gather_context
from app.services.planning.prefs import PlanPrefs, describe_prefs, prefs_from_dict
from app.services.planning.work_calendar import WorkCalendar, consume, worked_between
from app.services.shifts import get_shift_defs
from app.services.time_windows import DISPLAY_TZ


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@pytest.fixture()
def plant():
    db = make_session()
    f = seed_factory(db, T0)
    db.commit()
    return db, f


def _line(f, key, prod, qty, due_days, stock_key=None):
    p = f["products"][prod]
    return LineIn(key=key, product_id=p.id, product_label=p.name, qty=qty,
                  due=(T0 + timedelta(days=due_days)) if due_days is not None else None,
                  stock_key=stock_key)


def _plan(db, lines, stock=None, **prefs):
    ctx = gather_context(db, as_of=T0)
    stock = stock if stock is not None else {}
    return compute_plan_a2(ctx, lines, stock, prefs_from_dict(prefs))


def _prod_slots(res):
    return [s for s in res["slots"] if s["machine_id"]]


def _line_by_key(res, key):
    return next(ln for ln in res["lines"] if ln["key"] == key)


# ---------- stock allocation (is-kurallari §2) ----------


def test_stock_exactly_covers_two_orders_no_production_and_zero_warning(plant):
    db, f = plant
    pb = f["products"]["B"]
    lines = [_line(f, "b1", "B", 30, 2), _line(f, "b2", "B", 40, 5)]
    res = _plan(db, lines, {f"p:{pb.id}": 70})
    assert _prod_slots(res) == []
    assert [ln["status"] for ln in res["lines"]] == ["stock", "stock"]
    assert any("tam karşılıyor" in w and "70 → 0" in w for w in res["warnings"])


def test_early_due_served_from_stock_later_one_produced(plant):
    db, f = plant
    pa = f["products"]["A"]
    lines = [_line(f, "late", "A", 1000, 3), _line(f, "early", "A", 400, 1)]
    res = _plan(db, lines, {f"p:{pa.id}": 500}, group_same_product=False)
    early, late = _line_by_key(res, "early"), _line_by_key(res, "late")
    assert early["status"] == "stock" and early["stock_used"] == 400
    assert late["stock_used"] == 100 and late["need"] == 900
    assert sum(s["qty_planned"] for s in _prod_slots(res)) == 900
    assert any("sıfıra iniyor" in w for w in res["warnings"])


def test_legacy_double_counts_stock_but_a2_does_not(plant):
    db, f = plant
    pa = f["products"]["A"]
    lines = [_line(f, "a1", "A", 3000, 2), _line(f, "a2", "A", 2000, 9)]
    res = _plan(db, lines, {f"p:{pa.id}": 500}, group_same_product=False)
    assert sum(s["qty_planned"] for s in _prod_slots(res)) == 4500


def test_stock_allocation_off_uses_full_stock_per_line(plant):
    db, f = plant
    pa = f["products"]["A"]
    lines = [_line(f, "a1", "A", 3000, 2), _line(f, "a2", "A", 2000, 9)]
    res = _plan(db, lines, {f"p:{pa.id}": 500}, group_same_product=False, stock_allocation=False)
    assert sum(s["qty_planned"] for s in _prod_slots(res)) == 4000


# ---------- grouping ----------


def test_same_product_orders_grouped_into_one_run(plant):
    db, f = plant
    lines = [_line(f, "a1", "A", 1000, 2), _line(f, "a2", "A", 800, 6)]
    res = _plan(db, lines)
    slots = _prod_slots(res)
    assert len(slots) == 1
    assert slots[0]["meta"]["line_keys"] == ["a1", "a2"]
    assert slots[0]["qty_planned"] == 1800
    assert any("birlikte üretildi" in e for e in res["explanations"])
    a1, a2 = _line_by_key(res, "a1"), _line_by_key(res, "a2")
    assert a1["eta"] < a2["eta"]


def test_grouping_dropped_when_it_makes_another_order_late(plant):
    db, f = plant
    m1, m2, m3 = f["machines"]
    # Only M1 may run A and C → producing A1+A2 together pushes C past its tight due.
    db.query(MoldMachineCapability).delete()
    for mold in (f["molds"]["A1"], f["molds"]["C"]):
        db.add(MoldMachineCapability(mold_id=mold.id, machine_id=m1.id, allowed=True, priority=1))
    db.commit()
    lines = [
        _line(f, "a1", "A", 4000, 1),
        _line(f, "c", "C", 600, 1),
        _line(f, "a2", "A", 30000, 6),
    ]
    res = _plan(db, lines, split="no")
    assert any("ayrı üretildi" in e for e in res["explanations"])
    assert _line_by_key(res, "c")["status"] != "late"


# ---------- split (is-kurallari §6) ----------


def test_split_across_molds_when_single_slot_would_be_late(plant):
    db, f = plant
    lines = [_line(f, "big", "A", 30000, 1)]
    single = _plan(db, lines, split="no")
    split = _plan(db, lines, split="if_late")
    s_slots, p_slots = _prod_slots(single), _prod_slots(split)
    assert len(s_slots) == 1 and _line_by_key(single, "big")["status"] == "late"
    assert len(p_slots) >= 2
    assert len({s["mold_id"] for s in p_slots}) == len(p_slots)
    assert len({s["machine_id"] for s in p_slots}) == len(p_slots)
    assert sum(s["qty_planned"] for s in p_slots) == 30000
    assert max(s["suggested_end"] for s in p_slots) < s_slots[0]["suggested_end"]
    assert any("Bölme kapalı olduğu için" in e for e in single["explanations"])
    assert p_slots[0]["meta"]["parts"] == len(p_slots)


def test_no_split_when_single_slot_is_on_time(plant):
    db, f = plant
    res = _plan(db, [_line(f, "small", "A", 2000, 5)], split="if_late")
    assert len(_prod_slots(res)) == 1


def test_mold_never_runs_on_two_machines_at_once(plant):
    db, f = plant
    lines = [_line(f, "x", "A", 20000, 1), _line(f, "y", "C", 3000, 1)]
    res = _plan(db, lines, split="yes", group_same_product=False)
    by_mold: dict[int, list] = {}
    for s in _prod_slots(res):
        by_mold.setdefault(s["mold_id"], []).append((s["suggested_start"], s["suggested_end"]))
    for spans in by_mold.values():
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            assert b0 >= a1


# ---------- changeover ----------


def test_changeover_minutes_from_removal_plus_mount(plant):
    db, f = plant
    m1, m2, _ = f["machines"]
    # C only on M1 where K-A1 is mounted at T0 → removal 15 + mount 25
    db.query(MoldMachineCapability).filter_by(mold_id=f["molds"]["C"].id, machine_id=m2.id).delete()
    db.commit()
    res = _plan(db, [_line(f, "c", "C", 100, 3)])
    s = _prod_slots(res)[0]
    assert s["machine_id"] == m1.id
    assert s["meta"]["co_min"] == 40
    assert datetime.fromisoformat(s["meta"]["prod_start"]) - _aware(s["suggested_start"]) == timedelta(minutes=40)
    assert "Kalıp değişimi 40 dk" in s["reason"]
    assert res["kpis"]["changeovers"] == 1


def test_changeover_disabled_by_pref(plant):
    db, f = plant
    m1, m2, _ = f["machines"]
    db.query(MoldMachineCapability).filter_by(mold_id=f["molds"]["C"].id, machine_id=m2.id).delete()
    db.commit()
    res = _plan(db, [_line(f, "c", "C", 100, 3)], changeover=False)
    assert _prod_slots(res)[0]["meta"]["co_min"] == 0


def test_changeover_daytime_hard_rule(plant):
    db, f = plant
    m1, m2, _ = f["machines"]
    db.query(MoldMachineCapability).filter_by(mold_id=f["molds"]["C"].id, machine_id=m2.id).delete()
    db.commit()
    start = datetime(2026, 9, 10, 18, 0, tzinfo=DISPLAY_TZ)  # 18:00 local, outside 08–17
    ctx = gather_context(db, as_of=start)
    res = compute_plan_a2(
        ctx, [_line(f, "c", "C", 100, 3)], {},
        prefs_from_dict({"changeover_daytime": "hard", "changeover_window": "08:00-17:00"}),
    )
    co = _aware(_prod_slots(res)[0]["suggested_start"]).astimezone(DISPLAY_TZ)
    assert co.date() == datetime(2026, 9, 11).date() and co.hour == 8 and co.minute == 0


# ---------- calendar (hard vs soft) ----------


def _weekend_seconds(a, b):
    tot, t = 0.0, a
    while t < b:
        nxt = min(b, t + timedelta(minutes=15))
        if t.astimezone(DISPLAY_TZ).weekday() >= 5:
            tot += (nxt - t).total_seconds()
        t = nxt
    return tot


def test_weekend_off_is_hard(plant):
    db, f = plant
    # ~60 h of work from Thursday → would run through the weekend
    res = _plan(db, [_line(f, "w", "A", 41000, 10)], saturday="off", sunday="off", split="no")
    s = _prod_slots(res)[0]
    start, end = _aware(s["suggested_start"]), _aware(s["suggested_end"])
    assert end.astimezone(DISPLAY_TZ).weekday() == 0  # finishes on Monday
    ctx = gather_context(db, as_of=T0)
    cal = WorkCalendar(prefs_from_dict({"saturday": "off", "sunday": "off"}), ctx.shifts, T0)
    iv = cal.production(manual=False, strict=False)
    assert _weekend_seconds(start, end) > 0  # wall-clock spans the weekend…
    assert all(s.astimezone(DISPLAY_TZ).weekday() < 5 for s, _ in iv if s < end)  # …but no work in it


def test_weekend_avoid_is_relaxed_only_to_meet_due_and_explained(plant):
    db, f = plant
    relaxed = _plan(db, [_line(f, "w", "A", 41000, 2)], saturday="avoid", sunday="avoid", split="no")
    s = _prod_slots(relaxed)[0]
    assert "Cumartesi kullanıldı" in s["meta"]["relaxed"]
    assert "Termine yetişmek için Cumartesi kullanıldı" in s["reason"]
    assert any("esnetildi" in e for e in relaxed["explanations"])
    # same work with a far due date → weekend kept free
    kept = _plan(db, [_line(f, "w", "A", 41000, 20)], saturday="avoid", sunday="avoid", split="no")
    assert _prod_slots(kept)[0]["meta"]["relaxed"] == []
    assert _aware(_prod_slots(kept)[0]["suggested_end"]).astimezone(DISPLAY_TZ).weekday() == 0


def test_manual_mold_not_at_night_hard(plant):
    db, f = plant
    m3 = f["machines"][2]
    # K-B is a manual mold (only on M3); 40 s per piece → 3000 pcs ≈ 33 h
    res = _plan(db, [_line(f, "b", "B", 3000, 10)], manual_not_night="hard")
    s = _prod_slots(res)[0]
    assert s["machine_id"] == m3.id and s["meta"]["manual"] is True
    ctx = gather_context(db, as_of=T0)
    cal = WorkCalendar(prefs_from_dict({"manual_not_night": "hard"}), ctx.shifts, T0)
    iv = cal.production(manual=True, strict=False)
    a, b = datetime.fromisoformat(s["meta"]["prod_start"]), _aware(s["suggested_end"])
    for x, y in iv:
        if y <= a or x >= b:
            continue
        lx = x.astimezone(DISPLAY_TZ)
        assert lx.hour >= 8  # night shift 00:00–08:00 never used
    # automatic molds may still run at night
    assert cal.production(manual=False, strict=False)[0][1] - cal.production(manual=False, strict=False)[0][0] > timedelta(days=5)


def test_no_global_work_pattern_calendar_uses_shift_settings(plant):
    db, f = plant
    ctx = gather_context(db, as_of=T0)
    # prefs saved by older versions still carry work_pattern; it is ignored
    prefs = prefs_from_dict({"work_pattern": "2x8", "closed_days": ["2026-09-11"]})
    assert not hasattr(prefs, "work_pattern")
    cal = WorkCalendar(prefs, ctx.shifts, T0)
    iv = cal.production(manual=False, strict=False)
    day = datetime(2026, 9, 11, tzinfo=DISPLAY_TZ)
    assert worked_between(iv, day, day + timedelta(days=1)) == 0
    sat = datetime(2026, 9, 12, tzinfo=DISPLAY_TZ)
    assert worked_between(iv, sat, sat + timedelta(days=1)) == 24 * 3600  # all configured shifts


def test_line_hours_per_day_fills_day_shifts_first(plant):
    db, f = plant
    ctx = gather_context(db, as_of=T0)
    prefs = prefs_from_dict({"line_rules": {"a": {"hours_per_day": 8}, "c": {"hours_per_day": 12}}})
    cal = WorkCalendar(prefs, ctx.shifts, T0)
    day = datetime(2026, 9, 14, tzinfo=DISPLAY_TZ)
    eight = cal.production(manual=False, strict=False, rule=prefs.rule_for("a"))
    assert worked_between(eight, day, day + timedelta(days=1)) == 8 * 3600
    assert worked_between(eight, day + timedelta(hours=8), day + timedelta(hours=16)) == 8 * 3600
    twelve = cal.production(manual=False, strict=False, rule=prefs.rule_for("c"))
    assert worked_between(twelve, day + timedelta(hours=8), day + timedelta(hours=20)) == 12 * 3600
    # a line without a rule keeps the full shift calendar
    full = cal.production(manual=False, strict=False, rule=prefs.rule_for("zzz"))
    assert worked_between(full, day, day + timedelta(days=1)) == 24 * 3600


def test_line_hours_per_day_only_slows_that_line(plant):
    db, f = plant
    lines = [_line(f, "a", "A", 3000, 6), _line(f, "c", "C", 1200, 6)]
    base = _plan(db, lines)
    capped = _plan(db, lines, line_rules={"a": {"hours_per_day": 2}})
    slot_a = next(s for s in _prod_slots(capped) if "a" in s["meta"]["line_keys"])
    slot_c = next(s for s in _prod_slots(capped) if "c" in s["meta"]["line_keys"])
    assert slot_a["meta"]["hours_per_day"] == 2 and slot_c["meta"]["hours_per_day"] is None
    assert "günde en fazla 2 saat" in slot_a["reason"]
    # ~4.4 h of work at 2 h/day spans several days; the other line is unchanged
    assert _aware(slot_a["suggested_end"]) - _aware(slot_a["suggested_start"]) > timedelta(days=1)
    assert _line_by_key(capped, "a")["eta"] > _line_by_key(base, "a")["eta"]
    assert _line_by_key(capped, "c")["eta"] == _line_by_key(base, "c")["eta"]
    assert any("Siparişe özel" in x for x in capped["prefs_summary"])


def test_lines_with_different_rules_are_not_grouped(plant):
    db, f = plant
    lines = [_line(f, "a1", "A", 1000, 3), _line(f, "a2", "A", 1000, 4)]
    grouped = _plan(db, lines)
    assert len(_prod_slots(grouped)) == 1
    apart = _plan(db, lines, line_rules={"a2": {"hours_per_day": 6}})
    assert len(_prod_slots(apart)) == 2


def test_line_weekend_rule_overrides_global_default(plant):
    db, f = plant
    ctx = gather_context(db, as_of=T0)
    prefs = prefs_from_dict({"sunday": "work", "line_rules": {"a": {"sunday": "off"}}})
    cal = WorkCalendar(prefs, ctx.shifts, T0)
    sun = datetime(2026, 9, 13, tzinfo=DISPLAY_TZ)
    own = cal.production(manual=False, strict=False, rule=prefs.rule_for("a"))
    other = cal.production(manual=False, strict=False, rule=prefs.rule_for("b"))
    assert worked_between(own, sun, sun + timedelta(days=1)) == 0
    assert worked_between(other, sun, sun + timedelta(days=1)) == 24 * 3600


def test_line_rule_validation():
    with pytest.raises(ValueError):
        prefs_from_dict({"line_rules": {"a": {"hours_per_day": 30}}})
    with pytest.raises(ValueError):
        prefs_from_dict({"line_rules": {"a": {"saturday": "maybe"}}})
    p = prefs_from_dict({"line_rules": {"a": {"hours_per_day": ""}, "b": {"hours_per_day": "7.5"}}})
    assert list(p.line_rules) == ["b"] and p.line_rules["b"].hours_per_day == 7.5
    assert p.to_dict()["line_rules"]["b"]["hours_per_day"] == 7.5


def test_default_calendar_is_continuous(plant):
    db, f = plant
    cal = WorkCalendar(PlanPrefs(), get_shift_defs(db), T0)
    first, end = consume(cal.production(manual=False, strict=False), T0, 7 * 24 * 3600)
    assert first == T0 and end == T0 + timedelta(days=7)


# ---------- min stock ----------


def test_min_stock_is_held_back_from_orders(plant):
    db, f = plant
    pb = f["products"]["B"]
    pb.min_stock = 50
    db.commit()
    res = _plan(db, [_line(f, "b1", "B", 30, 3)], {f"p:{pb.id}": 70})
    ln = _line_by_key(res, "b1")
    assert ln["stock_used"] == 20 and ln["need"] == 10
    assert sum(s["qty_planned"] for s in _prod_slots(res)) == 10
    assert any("asgari stok" in w for w in res["warnings"])


def test_min_stock_above_stock_means_everything_is_produced(plant):
    db, f = plant
    pb = f["products"]["B"]
    pb.min_stock = 100
    db.commit()
    res = _plan(db, [_line(f, "b1", "B", 30, 3)], {f"p:{pb.id}": 70})
    assert _line_by_key(res, "b1")["need"] == 30
    assert any("asgari stok seviyesinde veya altında" in w for w in res["warnings"])


def test_min_stock_ignored_by_legacy_engine(plant):
    from app.services.planning.plan_engine import compute_plan

    db, f = plant
    pb = f["products"]["B"]
    lines = [_line(f, "b1", "B", 30, 3)]
    before = compute_plan(gather_context(db, as_of=T0), lines, {f"p:{pb.id}": 70}, None)
    pb.min_stock = 50
    db.commit()
    after = compute_plan(gather_context(db, as_of=T0), lines, {f"p:{pb.id}": 70}, None)
    assert before["slots"] == after["slots"]


def test_min_stock_refill_planned_early_when_orders_stay_on_time(plant):
    db, f = plant
    pb, pc = f["products"]["B"], f["products"]["C"]
    pc.min_stock = 200  # C has no open order; stock 0
    db.commit()
    lines = [_line(f, "b1", "B", 30, 3)]
    base = _plan(db, lines, {f"p:{pb.id}": 0})
    res = _plan(db, lines, {f"p:{pb.id}": 0, f"p:{pc.id}": 0})
    refill = [s for s in _prod_slots(res) if s["meta"]["refill"]]
    assert len(refill) == 1
    r = refill[0]
    assert r["product_id"] == pc.id and r["qty_planned"] == 200 and r["order_line_id"] is None
    assert r["meta"]["refill"] == "refill" and r["meta"]["line_keys"] == []
    assert "Asgari stok tamamla" in r["reason"] and "hemen planlandı" in r["reason"]
    assert _line_by_key(res, "b1")["status"] == _line_by_key(base, "b1")["status"]
    assert res["kpis"]["refill_qty"] == 200 and res["kpis"]["refill_now"] == 1
    assert any("hemen planlandı" in x for x in res["explanations"])


def test_min_stock_refill_moved_after_plan_end_when_it_would_make_orders_late(plant):
    db, f = plant
    pa = f["products"]["A"]
    m2 = f["machines"][1]
    pa.min_stock = 40000  # ~58 h on the only allowed machine
    db.commit()
    kw = dict(split="no", exclude_machine_ids=[m2.id], exclude_mold_ids=[f["molds"]["A2"].id])
    lines = [_line(f, "a1", "A", 3000, 1)]
    base = _plan(db, lines, {}, **kw)
    res = _plan(db, lines, {f"p:{pa.id}": 0}, **kw)
    order = next(s for s in _prod_slots(res) if not s["meta"]["refill"])
    refill = next(s for s in _prod_slots(res) if s["meta"]["refill"])
    assert refill["meta"]["refill"] == "refill_after" and refill["qty_planned"] == 40000
    assert _aware(refill["suggested_start"]) >= _aware(order["suggested_end"])
    assert "plan sonuna kondu" in refill["reason"]
    # order slot is exactly where it was without min stock
    assert _line_by_key(res, "a1")["status"] == _line_by_key(base, "a1")["status"] == "on_time"
    assert _line_by_key(res, "a1")["eta"] == _line_by_key(base, "a1")["eta"]
    assert res["kpis"]["refill_after"] == 1
    assert any("plan sonuna kondu" in x for x in res["explanations"])


def test_min_stock_refill_skipped_when_stock_unknown_or_enough(plant):
    db, f = plant
    pb, pc = f["products"]["B"], f["products"]["C"]
    pb.min_stock = 50
    pc.min_stock = 10
    db.commit()
    # B: 70 ≥ 50 → nothing to top up; C: stock not in the input → unknown, skipped
    res = _plan(db, [_line(f, "b1", "B", 10, 3)], {f"p:{pb.id}": 70})
    assert not [s for s in _prod_slots(res) if s["meta"]["refill"]]


def test_no_product_level_hours_default(plant):
    db, f = plant
    # only line rules exist; a product-keyed setting is not a preference
    p = prefs_from_dict({"product_hours": {str(f["products"]["A"].id): 4}, "line_rules": {"a1": {"hours_per_day": 4}}})
    assert not hasattr(p, "product_hours") and "product_hours" not in p.to_dict()
    lines = [_line(f, "a1", "A", 3000, 6), _line(f, "a2", "A", 3000, 9)]
    res = _plan(db, lines, line_rules={"a1": {"hours_per_day": 4}})
    by_key = {k: s for s in _prod_slots(res) for k in s["meta"]["line_keys"]}
    assert by_key["a1"]["meta"]["hours_per_day"] == 4
    # same product, no line rule → full shift calendar (continuous 24 h)
    a2 = by_key["a2"]
    assert a2["meta"]["hours_per_day"] is None
    span = _aware(a2["suggested_end"]) - datetime.fromisoformat(a2["meta"]["prod_start"])
    assert span <= timedelta(hours=5)


# ---------- cycle source ----------


def test_cycle_without_history_uses_target_not_mold_average(plant):
    db, f = plant
    kc = f["molds"]["C"]
    m1, m2 = f["machines"][0], f["machines"][1]
    kc.avg_cycle_s = 99.0  # running average (would include post-T0 cycles)
    db.commit()
    # K-C has history only on M2 → force M1 (no history there)
    res = _plan(db, [_line(f, "c", "C", 600, 6)], exclude_machine_ids=[m2.id])
    slot = _prod_slots(res)[0]
    assert slot["machine_id"] == m1.id
    assert slot["meta"]["cycle_s"] == 30.0  # target
    assert "hedef çevrimi 30 sn" in slot["reason"]


def test_cycle_without_history_or_target_never_uses_average_in_backtest(plant):
    db, f = plant
    kc = f["molds"]["C"]
    m2 = f["machines"][1]
    kc.avg_cycle_s = 99.0
    kc.target_cycle_s = None
    db.commit()
    res = _plan(db, [_line(f, "c", "C", 600, 6)], exclude_machine_ids=[m2.id])
    assert _prod_slots(res)[0]["meta"]["cycle_s"] == 30.0
    # live (no T0) has no look-ahead problem → the mold average is a last resort
    live = compute_plan_a2(gather_context(db), [_line(f, "c", "C", 600, 6)], {},
                           prefs_from_dict({"exclude_machine_ids": [m2.id]}))
    assert _prod_slots(live)[0]["meta"]["cycle_s"] == 99.0


def test_cycle_source_history_prefers_history_when_present(plant):
    db, f = plant
    m1 = f["machines"][0]
    res = _plan(db, [_line(f, "c", "C", 600, 6)], exclude_machine_ids=[m1.id])
    slot = _prod_slots(res)[0]
    assert slot["meta"]["cycle_s"] == 31.0
    tgt = _plan(db, [_line(f, "c", "C", 600, 6)], exclude_machine_ids=[m1.id], cycle_source="target")
    assert _prod_slots(tgt)[0]["meta"]["cycle_s"] == 30.0


# ---------- downtime risk ----------


def test_risky_machine_avoided_for_tight_due(plant):
    db, f = plant
    m1, m2, m3 = f["machines"]
    db.add(MoldMachineCapability(mold_id=f["molds"]["B"].id, machine_id=m2.id, allowed=True, priority=2))
    db.commit()
    avoid = _plan(db, [_line(f, "b", "B", 30, 2)], risky_machines="avoid")
    ignore = _plan(db, [_line(f, "b", "B", 30, 2)], risky_machines="ignore")
    assert _prod_slots(ignore)[0]["machine_id"] == m3.id
    assert _prod_slots(avoid)[0]["machine_id"] == m2.id
    assert "kez durdu" in _prod_slots(ignore)[0]["reason"]


def test_excluded_machine_and_mold_never_used(plant):
    db, f = plant
    m1 = f["machines"][0]
    res = _plan(db, [_line(f, "a", "A", 5000, 3)], exclude_machine_ids=[m1.id],
                exclude_mold_ids=[f["molds"]["A2"].id])
    for s in _prod_slots(res):
        assert s["machine_id"] != m1.id and s["mold_id"] != f["molds"]["A2"].id


def test_no_slot_starts_before_plan_start(plant):
    db, f = plant
    lines = [_line(f, "a", "A", 9000, 1), _line(f, "b", "B", 500, 2), _line(f, "c", "C", 2000, 3)]
    res = _plan(db, lines, saturday="avoid", manual_not_night="soft", changeover_daytime="soft")
    assert all(_aware(s["suggested_start"]) >= T0 for s in _prod_slots(res))


# ---------- prefs ----------


def test_prefs_validation_and_summary():
    with pytest.raises(ValueError):
        prefs_from_dict({"priority": "fastest"})
    with pytest.raises(ValueError):
        prefs_from_dict({"changeover_window": "17:00-08:00"})
    with pytest.raises(ValueError):
        prefs_from_dict({"closed_days": ["2026-13-01"]})
    assert prefs_from_dict(None) is None
    p = prefs_from_dict({"saturday": "off", "manual_not_night": "hard"})
    text = " | ".join(describe_prefs(p))
    assert "Cumartesi: Çalışılmaz" in text and "Kesin kural" in text
    assert describe_prefs(None) == ["Tercih seçilmedi — standart plan (önceki davranış)"]


def test_live_plan_with_prefs_stores_prefs_and_result():
    db = make_session()
    now = datetime.now(timezone.utc)
    f = seed_factory(db, now)
    add_open_orders(db, f, now)
    db.commit()
    plan = build_plan(db, prefs=prefs_from_dict({"priority": "min_changeover"}))
    assert plan.status == "active"
    assert json.loads(plan.prefs_json)["priority"] == "min_changeover"
    result = json.loads(plan.result_json)
    assert result["kpis"]["lines"] == 5
    assert {ln["status"] for ln in result["lines"]} <= {"stock", "on_time", "risky", "late", "unplanned"}


def test_patch_product_min_stock_marks_live_plan_stale():
    from app.api.routers.products import ProductPatch, patch_product

    db = make_session()
    now = datetime.now(timezone.utc)
    f = seed_factory(db, now)
    add_open_orders(db, f, now)
    db.commit()
    plan = build_plan(db)
    pb = f["products"]["B"]
    out = patch_product(pb.id, ProductPatch(min_stock=40), db=db, user=None)
    assert out.min_stock == 40
    db.refresh(plan)
    assert plan.stale_reason == "Asgari stok değişti"
    assert patch_product(pb.id, ProductPatch(min_stock=0), db=db, user=None).min_stock is None
