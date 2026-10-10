"""Compare plan slots vs actual mold assignments / runs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Machine, MachineDowntime, Mold, MoldRun, PlanRun, PlanSlot
from app.db.models import json_dumps  # noqa: F401 — keep import style consistent


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _downtime_covers(db: Session, machine_id: int, at: datetime) -> bool:
    at = _aware(at)  # type: ignore[assignment]
    rows = (
        db.query(MachineDowntime)
        .filter(MachineDowntime.machine_id == int(machine_id), MachineDowntime.start_at <= at)
        .all()
    )
    for f in rows:
        if f.end_at is None or _aware(f.end_at) >= at:
            return True
    return False


def compare_plan(db: Session, plan_run_id: int) -> dict:
    plan = db.get(PlanRun, plan_run_id)
    if not plan:
        raise ValueError("Plan bulunamadı")
    slots = (
        db.query(PlanSlot)
        .filter(PlanSlot.plan_run_id == plan_run_id, PlanSlot.machine_id.isnot(None))
        .all()
    )
    rows: list[dict] = []
    for slot in slots:
        # Find actual assignment for this mold near suggested_start
        actual_machine = None
        actual_at = None
        events = (
            db.query(Event)
            .filter(Event.type == "mold_assigned")
            .order_by(Event.created_at.desc())
            .limit(200)
            .all()
        )
        for ev in events:
            import json

            try:
                payload = json.loads(ev.payload or "{}")
            except json.JSONDecodeError:
                continue
            if int(payload.get("mold_id") or 0) != int(slot.mold_id or 0):
                continue
            actual_machine = ev.machine_id
            actual_at = _aware(ev.created_at)
            break

        # Prefer mold_run start for this mold
        run = (
            db.query(MoldRun)
            .filter(MoldRun.mold_id == slot.mold_id)
            .order_by(MoldRun.started_at.desc())
            .first()
        )
        if run:
            actual_machine = run.machine_id
            actual_at = _aware(run.started_at)

        delay_h = None
        if actual_at and slot.suggested_start:
            delay_h = round(
                (actual_at - _aware(slot.suggested_start)).total_seconds() / 3600.0,  # type: ignore[arg-type]
                2,
            )
        same_machine = (
            actual_machine is not None
            and slot.machine_id is not None
            and int(actual_machine) == int(slot.machine_id)
        )
        suggested_faulted = False
        if slot.machine_id and slot.suggested_start:
            suggested_faulted = _downtime_covers(db, int(slot.machine_id), slot.suggested_start)

        # Estimated earlier finish if suggested machine had been used
        earlier_h = None
        if (
            not same_machine
            and delay_h is not None
            and delay_h > 0
            and not suggested_faulted
            and slot.suggested_start
            and slot.suggested_end
        ):
            planned_h = (
                _aware(slot.suggested_end) - _aware(slot.suggested_start)  # type: ignore[operator]
            ).total_seconds() / 3600.0
            earlier_h = round(delay_h, 2)
            _ = planned_h  # reserved for richer estimate

        note = None
        if suggested_faulted and not same_machine:
            note = "Önerilen makine duruştaydı; sapma kayıp sayılmaz"
        elif same_machine and (delay_h or 0) <= 0.25:
            note = "Öneriye yakın uygulandı"
        elif not same_machine and actual_machine:
            note = f"Farklı makine: önerilen #{slot.machine_id}, gerçek #{actual_machine}"

        rows.append(
            {
                "slot_id": slot.id,
                "order_line_id": slot.order_line_id,
                "mold_id": slot.mold_id,
                "product_id": slot.product_id,
                "suggested_machine_id": slot.machine_id,
                "suggested_start": slot.suggested_start.isoformat() if slot.suggested_start else None,
                "suggested_end": slot.suggested_end.isoformat() if slot.suggested_end else None,
                "qty_planned": slot.qty_planned,
                "actual_machine_id": actual_machine,
                "actual_start": actual_at.isoformat() if actual_at else None,
                "same_machine": same_machine,
                "delay_hours": delay_h,
                "earlier_finish_hours_if_followed": earlier_h,
                "suggested_machine_was_faulted": suggested_faulted,
                "note": note,
                "reason": slot.reason,
            }
        )

    return {
        "plan_run_id": plan.id,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Compare v1.1 — backtest plan vs what actually happened in the window
# ---------------------------------------------------------------------------

CYCLE_SLOWER = 1.10
LATE_START_H = 4.0
ACTUAL_TAIL_DAYS = 14


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dt(v: str | datetime | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return _aware(v)
    return _aware(datetime.fromisoformat(v))


def _plan_lines(plan: PlanRun, slots: list[PlanSlot]) -> list[dict]:
    """Per-order plan view; falls back to slots for legacy (no-prefs) backtests."""
    from app.services.planning.plan_a2 import deadline_of

    result = _loads(plan.result_json) or {}
    if result.get("lines"):
        return result["lines"]
    inputs = _loads(plan.input_json) or {}
    by_key: dict[str, list[PlanSlot]] = {}
    for s in slots:
        by_key.setdefault(s.line_key or f"o:{s.order_line_id}", []).append(s)
    out = []
    for ln in inputs.get("lines") or []:
        ss = by_key.get(ln["key"], [])
        prod = [s for s in ss if s.machine_id]
        due = _dt(ln.get("due_date"))
        deadline = deadline_of(due)
        eta = max((_aware(s.suggested_end) for s in prod), default=None)
        need = sum(int(s.qty_planned or 0) for s in prod)
        if prod:
            status = "late" if deadline and eta and eta > deadline else "on_time"
        else:
            reason = (ss[0].reason or "") if ss else ""
            status = "stock" if reason.startswith("Stok") else "unplanned"
        out.append(
            {
                "key": ln["key"],
                "product_id": ln.get("product_id"),
                "product_label": ln.get("product_name") or ln.get("product_name_raw"),
                "customer": ln.get("customer"),
                "qty": ln["qty"],
                "due": due.isoformat() if due else None,
                "deadline": deadline.isoformat() if deadline else None,
                "need": need,
                "stock_used": max(0, int(ln["qty"]) - need) if status == "stock" else 0,
                "eta": eta.isoformat() if eta else None,
                "status": status,
                "reason": ss[0].reason if ss else None,
                "machines": [],
            }
        )
    return out


def _actual_events(
    db: Session, product_id: int, t0: datetime, t1: datetime
) -> tuple[list[tuple[datetime, int, int, int]], list[float], list[MoldRun]]:
    """(t_end, qty, machine_id, mold_id) events for a product in [t0, t1], cycle times, runs."""
    from app.services.planning import effective_cavity

    molds = {m.id: m for m in db.query(Mold).filter(Mold.product_id == int(product_id)).all()}
    if not molds:
        return [], [], []
    runs = (
        db.query(MoldRun)
        .filter(MoldRun.mold_id.in_(list(molds)), MoldRun.started_at < t1)
        .order_by(MoldRun.started_at.asc())
        .all()
    )
    runs = [r for r in runs if r.ended_at is None or _aware(r.ended_at) > t0]
    cycles = (
        db.query(Cycle)
        .filter(
            Cycle.mold_id.in_(list(molds)),
            Cycle.is_counted.is_(True),
            Cycle.t_end >= t0,
            Cycle.t_end <= t1,
        )
        .order_by(Cycle.t_end.asc())
        .all()
    )
    events: list[tuple[datetime, int, int, int]] = []
    times: list[float] = []
    covered: set[int] = set()
    for c in cycles:
        t = _aware(c.t_end)
        run = next(
            (
                r
                for r in runs
                if r.machine_id == c.machine_id
                and r.mold_id == c.mold_id
                and _aware(r.started_at) <= t
                and (r.ended_at is None or _aware(r.ended_at) >= t)
            ),
            None,
        )
        cav = (run.active_cavity_snapshot if run and run.active_cavity_snapshot else None) or effective_cavity(
            molds[int(c.mold_id)]
        )
        if run:
            covered.add(run.id)
        events.append((t, int(cav), int(c.machine_id), int(c.mold_id)))
        times.append(float(c.cycle_time_s))
    for r in runs:
        if r.id in covered or not r.product_added or r.ended_at is None:
            continue
        rs, re_ = _aware(r.started_at), _aware(r.ended_at)
        span = max(1.0, (re_ - rs).total_seconds())
        inside = max(0.0, (min(re_, t1) - max(rs, t0)).total_seconds())
        qty = int(round(int(r.product_added) * inside / span))
        if qty > 0:
            events.append((min(re_, t1), qty, int(r.machine_id), int(r.mold_id)))
    events.sort(key=lambda e: e[0])
    return events, times, runs


def _downtimes_overlapping(db: Session, machine_id: int, a: datetime, b: datetime) -> list[MachineDowntime]:
    rows = (
        db.query(MachineDowntime)
        .filter(MachineDowntime.machine_id == int(machine_id), MachineDowntime.start_at < b)
        .all()
    )
    return [r for r in rows if r.end_at is None or _aware(r.end_at) > a]


def compare_backtest(db: Session, plan_id: int, *, now: datetime | None = None) -> dict:
    from app.services.planning.plan_a2 import STATUS_LABELS, fmt_day, fmt_dt, fmt_qty

    plan = db.get(PlanRun, plan_id)
    if not plan or plan.status != "backtest":
        raise ValueError("Geçmiş deneme bulunamadı")
    t0 = _aware(plan.as_of)
    horizon = _aware(plan.horizon_end) or (t0 + timedelta(days=45))
    now = _aware(now) or datetime.now(timezone.utc)
    window_end = min(now, horizon + timedelta(days=ACTUAL_TAIL_DAYS))
    slots = db.query(PlanSlot).filter(PlanSlot.plan_run_id == plan.id).order_by(PlanSlot.id).all()
    lines = _plan_lines(plan, slots)
    machine_names = {m.id: m.name for m in db.query(Machine).all()}
    mold_names = {m.id: (m.name or f"Kalıp #{m.id}") for m in db.query(Mold).all()}

    slots_by_line: dict[str, list[PlanSlot]] = {}
    for s in slots:
        if not s.machine_id:
            continue
        meta = _loads(s.meta_json) or {}
        for k in meta.get("line_keys") or [s.line_key]:
            slots_by_line.setdefault(k, []).append(s)

    rows: list[dict] = []
    per_product: dict[int, list[dict]] = {}
    for ln in lines:
        if ln.get("product_id") and int(ln.get("need") or 0) > 0:
            per_product.setdefault(int(ln["product_id"]), []).append(ln)

    actual_by_key: dict[str, dict] = {}
    product_events: dict[int, list] = {}
    for pid, group in per_product.items():
        events, times, _runs = _actual_events(db, pid, t0, window_end)
        product_events[pid] = events
        avg_actual_cycle = (sum(times) / len(times)) if times else None
        far = datetime.max.replace(tzinfo=timezone.utc)
        group = sorted(group, key=lambda ln: (_dt(ln.get("deadline")) or far, ln["key"]))
        produced = 0
        i = 0
        prev_finish: datetime | None = None
        for ln in group:
            need = int(ln["need"])
            cum_needed = sum(int(x["need"]) for x in group[: group.index(ln) + 1])
            finish = None
            machines: dict[int, int] = {}
            first_t = None
            while i < len(events) and produced < cum_needed:
                t, q, mid, _mold = events[i]
                produced += q
                machines[mid] = machines.get(mid, 0) + q
                first_t = first_t or t
                i += 1
                if produced >= cum_needed:
                    finish = t
            if produced >= cum_needed and finish is None:
                finish = prev_finish
            got = max(0, min(need, produced - (cum_needed - need)))
            actual_by_key[ln["key"]] = {
                "qty": got,
                "finish": finish,
                "start": first_t,
                "machines": machines,
                "avg_cycle_s": round(avg_actual_cycle, 2) if avg_actual_cycle else None,
            }
            prev_finish = finish or prev_finish

    on_time_plan = on_time_actual = dated = 0
    planned_total = actual_total = 0
    match_hits = match_total = 0
    for ln in lines:
        key = ln["key"]
        deadline = _dt(ln.get("deadline"))
        plan_slots = slots_by_line.get(key, [])
        act = actual_by_key.get(key)
        need = int(ln.get("need") or 0)
        plan_status = ln.get("status")
        planned_machines = sorted({int(s.machine_id) for s in plan_slots})
        planned_cycle = None
        if plan_slots:
            metas = [(_loads(s.meta_json) or {}) for s in plan_slots]
            cyc = [(m.get("cycle_s"), int(s.qty_planned or 0)) for m, s in zip(metas, plan_slots) if m.get("cycle_s")]
            if cyc:
                planned_cycle = sum(c * q for c, q in cyc) / max(1, sum(q for _, q in cyc))
        actual_status = None
        delay_h = None
        reasons: list[str] = []
        if plan_status == "stock":
            actual_status = "stock"
        elif plan_status == "unplanned" or need <= 0:
            actual_status = "unknown"
        elif act is not None:
            planned_total += need
            actual_total += act["qty"]
            if act["finish"] is not None and deadline is not None:
                delay_h = round((act["finish"] - deadline).total_seconds() / 3600.0, 1)
                actual_status = "late" if delay_h > 0 else "on_time"
            elif act["finish"] is not None:
                actual_status = "on_time"
            elif deadline is not None and window_end > deadline:
                actual_status = "late"
                delay_h = round((window_end - deadline).total_seconds() / 3600.0, 1)
            else:
                actual_status = "open"
            for s in plan_slots:
                match_total += 1
                if int(s.machine_id) in (act["machines"] or {}):
                    match_hits += 1
            # Deviation reasons — data only
            for s in plan_slots:
                a, b = _aware(s.suggested_start), _aware(s.suggested_end)
                for d in _downtimes_overlapping(db, int(s.machine_id), a, b):
                    if _aware(d.start_at) >= window_end:
                        continue
                    end_txt = fmt_dt(_aware(d.end_at)) if d.end_at else "açık"
                    reasons.append(
                        f"Planlanan makine {machine_names.get(s.machine_id, s.machine_id)} "
                        f"{fmt_dt(_aware(d.start_at))} – {end_txt} arası duruştaydı"
                        + (f" ({d.note})" if d.note else "")
                    )
            if planned_cycle and act["avg_cycle_s"] and act["avg_cycle_s"] > planned_cycle * CYCLE_SLOWER:
                reasons.append(f"Çevrim süresi uzadı (plan {planned_cycle:.0f} sn, gerçek {act['avg_cycle_s']:.0f} sn)")
            if act["machines"] and planned_machines and not set(planned_machines) & set(act["machines"]):
                names = ", ".join(machine_names.get(m, f"#{m}") for m in act["machines"])
                reasons.append(f"Farklı makinede üretildi ({names})")
            if act["qty"] < need:
                reasons.append(f"Pencerede yeterli üretim yok ({fmt_qty(act['qty'])} / {fmt_qty(need)} adet)")
            plan_prod_start = min(
                (_dt((_loads(s.meta_json) or {}).get("prod_start")) or _aware(s.suggested_start) for s in plan_slots),
                default=None,
            )
            if act["start"] and plan_prod_start and (act["start"] - plan_prod_start).total_seconds() > LATE_START_H * 3600:
                reasons.append(f"Üretime geç başlandı (plan {fmt_dt(plan_prod_start)}, gerçek {fmt_dt(act['start'])})")
            if actual_status == "late" and not reasons:
                reasons.append("Neden bilinmiyor (kayıtlarda açıklayan veri yok)")
        if deadline is not None and plan_status in ("on_time", "risky", "late") and actual_status in ("on_time", "late"):
            dated += 1
            on_time_plan += int(plan_status != "late")
            on_time_actual += int(actual_status == "on_time")

        actual_machines = []
        if act and act["machines"]:
            actual_machines = [
                machine_names.get(m, f"#{m}") for m, _q in sorted(act["machines"].items(), key=lambda kv: -kv[1])
            ]
        summary = _summary_text(ln, plan_slots, act, actual_status, delay_h, reasons, machine_names, fmt_dt, fmt_day, fmt_qty)
        rows.append(
            {
                "key": key,
                "product_id": ln.get("product_id"),
                "product_label": ln.get("product_label"),
                "customer": ln.get("customer"),
                "qty": ln.get("qty"),
                "need": need,
                "stock_used": ln.get("stock_used"),
                "deadline": ln.get("deadline"),
                "plan": {
                    "status": plan_status,
                    "status_label": STATUS_LABELS.get(plan_status, plan_status),
                    "eta": ln.get("eta"),
                    "machines": [machine_names.get(m, f"#{m}") for m in planned_machines],
                    "molds": sorted({mold_names.get(int(s.mold_id), "") for s in plan_slots if s.mold_id}),
                    "qty": sum(int(s.qty_planned or 0) for s in plan_slots) if plan_slots else 0,
                    "cycle_s": round(planned_cycle, 1) if planned_cycle else None,
                    "reason": ln.get("reason"),
                },
                "actual": {
                    "status": actual_status,
                    "status_label": ACTUAL_LABELS.get(actual_status or "", actual_status),
                    "finish": act["finish"].isoformat() if act and act["finish"] else None,
                    "start": act["start"].isoformat() if act and act["start"] else None,
                    "qty": act["qty"] if act else None,
                    "machines": actual_machines,
                    "cycle_s": act["avg_cycle_s"] if act else None,
                    "delay_h": delay_h,
                },
                "risk": _risk_label(plan_status, actual_status),
                "reasons": reasons,
                "summary": summary,
            }
        )

    plan_kpis = (_loads(plan.result_json) or {}).get("kpis") or {}
    actual_changeovers, gantt = _gantt_and_changeovers(
        db, plan, slots, t0, window_end, set(per_product), machine_names, mold_names
    )
    all_events = [e for evs in product_events.values() for e in evs]
    finishes = [a["finish"] for a in actual_by_key.values() if a["finish"]]
    kpis = {
        "on_time_plan": round(on_time_plan / dated, 3) if dated else None,
        "on_time_actual": round(on_time_actual / dated, 3) if dated else None,
        "dated_lines": dated,
        "makespan_plan_h": plan_kpis.get("makespan_h"),
        "makespan_actual_h": round((max(finishes) - min(e[0] for e in all_events)).total_seconds() / 3600.0, 1)
        if finishes and all_events
        else None,
        "changeovers_plan": plan_kpis.get("changeovers", sum(1 for s in slots if (_loads(s.meta_json) or {}).get("co_min"))),
        "changeovers_actual": actual_changeovers,
        "machine_match": round(match_hits / match_total, 3) if match_total else None,
        "qty_deviation": round((planned_total - actual_total) / planned_total, 3) if planned_total else None,
        "planned_qty": planned_total,
        "actual_qty": actual_total,
    }
    return {
        "plan_run_id": plan.id,
        "as_of": t0.isoformat(),
        "horizon_end": horizon.isoformat(),
        "window_end": window_end.isoformat(),
        "kpis": kpis,
        "rows": rows,
        "gantt": gantt,
    }


ACTUAL_LABELS = {
    "stock": "Stoktan",
    "on_time": "Zamanında bitti",
    "late": "Gecikti",
    "open": "Devam ediyor",
    "unknown": "Veri yok",
}


def _risk_label(plan_status: str | None, actual_status: str | None) -> dict:
    if actual_status == "late":
        return {"level": "late", "label": "Gecikti"}
    if plan_status == "late":
        return {"level": "late", "label": "Gecikir (plana göre)"}
    if plan_status == "risky" or actual_status == "open":
        return {"level": "risky", "label": "Riskli"}
    if plan_status == "unplanned":
        return {"level": "unknown", "label": "Planlanamadı"}
    return {"level": "ok", "label": "Zamanında"}


def _summary_text(ln, plan_slots, act, actual_status, delay_h, reasons, machine_names, fmt_dt, fmt_day, fmt_qty) -> dict:
    need = int(ln.get("need") or 0)
    if ln.get("status") == "stock":
        planned = f"Stoktan: {fmt_qty(ln.get('qty') or 0)} adet"
    elif plan_slots:
        ms = ", ".join(sorted({machine_names.get(int(s.machine_id), f'#{s.machine_id}') for s in plan_slots}))
        planned = f"{ms}, {fmt_qty(need)} adet, {fmt_day(_dt(ln.get('eta')))} bitiş"
    else:
        planned = ln.get("reason") or "Plan yok"
    if actual_status == "stock":
        actual = "Stoktan karşılandı"
    elif act and act["machines"]:
        ms = ", ".join(machine_names.get(m, f"#{m}") for m in act["machines"])
        when = fmt_day(act["finish"]) if act["finish"] else "tamamlanmadı"
        actual = f"{ms}, {fmt_qty(act['qty'])} adet, {when}"
    elif act is not None:
        actual = "Pencerede bu ürün için üretim kaydı yok"
    else:
        actual = "—"
    if actual_status == "late" and delay_h is not None:
        days = delay_h / 24.0
        risk = (f"{days:.0f} gün gecikme" if days >= 1 else f"{max(1, round(delay_h))} saat gecikme")
        if reasons:
            risk += " — neden: " + reasons[0]
    elif actual_status == "on_time":
        risk = "Zamanında"
    elif ln.get("status") == "late":
        risk = "Plana göre de gecikecekti"
    else:
        risk = "—"
    return {"planned": planned, "actual": actual, "risk": risk}


def _gantt_and_changeovers(db, plan, slots, t0, window_end, product_ids, machine_names, mold_names):
    plan_end = max((_aware(s.suggested_end) for s in slots if s.suggested_end), default=t0)
    machine_ids = {int(s.machine_id) for s in slots if s.machine_id}
    relevant_molds = {
        m.id for m in db.query(Mold).filter(Mold.product_id.in_(list(product_ids) or [-1])).all()
    }
    runs = (
        db.query(MoldRun)
        .filter(MoldRun.started_at < window_end)
        .order_by(MoldRun.machine_id.asc(), MoldRun.started_at.asc())
        .all()
    )
    runs = [r for r in runs if r.ended_at is None or _aware(r.ended_at) > t0]
    for r in runs:
        if r.mold_id in relevant_molds:
            machine_ids.add(int(r.machine_id))
    last_actual = max(
        (min(_aware(r.ended_at) if r.ended_at else window_end, window_end) for r in runs if r.machine_id in machine_ids),
        default=t0,
    )
    end = max(plan_end, min(last_actual, window_end), t0 + timedelta(days=1))
    from app.services.planning.engine import current_molds_at

    mounted_at_t0 = current_molds_at(db, t0)
    changeovers = 0
    rows = []
    for mid in sorted(machine_ids, key=lambda m: machine_names.get(m, "")):
        planned = []
        for s in slots:
            if s.machine_id != mid or not s.suggested_start:
                continue
            meta = _loads(s.meta_json) or {}
            planned.append(
                {
                    "start": _aware(s.suggested_start).isoformat(),
                    "end": _aware(s.suggested_end).isoformat(),
                    "prod_start": meta.get("prod_start"),
                    "label": f"{mold_names.get(s.mold_id, '')} · {s.qty_planned}"
                    + (" · asgari stok" if meta.get("refill") else ""),
                    "line_keys": meta.get("line_keys") or [s.line_key],
                    "refill": meta.get("refill"),
                }
            )
        actual = []
        prev_mold = mounted_at_t0.get(mid)
        for r in [r for r in runs if r.machine_id == mid]:
            a = max(_aware(r.started_at), t0)
            b = min(_aware(r.ended_at) if r.ended_at else window_end, window_end)
            if b <= a:
                continue
            if prev_mold is not None and prev_mold != r.mold_id and _aware(r.started_at) > t0:
                changeovers += 1
            prev_mold = r.mold_id
            actual.append(
                {
                    "start": a.isoformat(),
                    "end": b.isoformat(),
                    "label": mold_names.get(r.mold_id, ""),
                    "relevant": r.mold_id in relevant_molds,
                }
            )
        downs = [
            {
                "start": max(_aware(d.start_at), t0).isoformat(),
                "end": min(_aware(d.end_at) if d.end_at else window_end, window_end).isoformat(),
                "label": d.note or "Duruş",
            }
            for d in _downtimes_overlapping(db, mid, t0, end)
            if _aware(d.start_at) < window_end
        ]
        rows.append(
            {
                "machine_id": mid,
                "machine_name": machine_names.get(mid, f"#{mid}"),
                "planned": planned,
                "actual": actual,
                "downtimes": downs,
            }
        )
    return changeovers, {"start": t0.isoformat(), "end": end.isoformat(), "rows": rows}
