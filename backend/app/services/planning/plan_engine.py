"""Plan suggestion engine — recommendation only, never auto-assigns.

Split into three steps so the same logic serves the live plan and backtests:
  1. `gather_context` reads machines / molds / stats / downtimes (optionally as of T0)
  2. `compute_plan` turns order lines + stock into slot dicts (no DB writes)
  3. `build_plan` / `write_plan_run` persist a PlanRun with its slots
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Machine,
    Mold,
    MoldMachineCapability,
    OrderLine,
    PlanRun,
    PlanSlot,
)
from app.services.planning import effective_cavity
from app.services.planning.engine import (
    current_molds_at,
    downtime_machine_ids_at,
    machine_downtime_risk,
    mold_machine_stats,
    open_downtime_machine_ids,
)
from app.services.planning.plan_state import supersede_open_plans
from app.services.planning.stock import current_stock

STATS_DAYS = 45


@dataclass
class LineIn:
    key: str
    product_id: int | None
    product_label: str
    qty: int
    due: datetime | None
    order_line_id: int | None = None
    customer: str | None = None
    stock_key: str | None = None

    def skey(self) -> str:
        return self.stock_key or (f"p:{self.product_id}" if self.product_id else f"raw:{self.product_label}")


@dataclass
class PlanContext:
    now: datetime
    as_of: datetime | None
    machines: dict[int, Machine]
    machine_names: dict[int, str]
    down_ids: set[int]
    stats: dict[tuple[int, int], dict]
    molds_by_product: dict[int, list[Mold]]
    mold_names: dict[int, str]
    caps_by_mold: dict[int, list[MoldMachineCapability]]
    current_mold: dict[int, int]
    risk: dict[int, dict]
    shifts: list = field(default_factory=list)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def gather_context(db: Session, *, as_of: datetime | None = None, start: datetime | None = None) -> PlanContext:
    """Snapshot planner inputs. `as_of` (backtest T0) hides everything after T0."""
    from app.services.shifts import get_shift_defs

    ref = _aware(as_of) or datetime.now(timezone.utc)
    now = _aware(start) or ref
    if as_of is not None:
        stats_rows = mold_machine_stats(db, days=STATS_DAYS, as_of=ref)
        down = downtime_machine_ids_at(db, ref)
        current = current_molds_at(db, ref)
    else:
        stats_rows = mold_machine_stats(db, days=STATS_DAYS)
        down = open_downtime_machine_ids(db)
        current = {
            int(m.id): int(m.current_mold_id)
            for m in db.query(Machine).filter(Machine.current_mold_id.isnot(None)).all()
        }
    all_machines = db.query(Machine).all()
    machines = {
        m.id: m
        for m in db.query(Machine).filter(Machine.enabled.is_(True)).all()
        if m.id not in down
    }
    molds = db.query(Mold).filter(Mold.status != "ignored").order_by(Mold.id.asc()).all()
    by_product: dict[int, list[Mold]] = {}
    for m in molds:
        if m.product_id:
            by_product.setdefault(int(m.product_id), []).append(m)
    caps: dict[int, list[MoldMachineCapability]] = {}
    for c in (
        db.query(MoldMachineCapability)
        .filter(MoldMachineCapability.allowed.is_(True))
        .order_by(MoldMachineCapability.priority.asc(), MoldMachineCapability.id.asc())
        .all()
    ):
        caps.setdefault(int(c.mold_id), []).append(c)
    return PlanContext(
        now=now,
        as_of=_aware(as_of),
        machines=machines,
        machine_names={int(m.id): m.name for m in all_machines},
        down_ids=down,
        stats={(s["mold_id"], s["machine_id"]): s for s in stats_rows},
        molds_by_product=by_product,
        mold_names={int(m.id): (m.name or f"Kalıp #{m.id}") for m in db.query(Mold).all()},
        caps_by_mold=caps,
        current_mold=current,
        risk=machine_downtime_risk(db, ref),
        shifts=get_shift_defs(db),
    )


def open_order_lines(db: Session) -> list[LineIn]:
    rows = (
        db.query(OrderLine)
        .filter(OrderLine.status == "open")
        .order_by(OrderLine.due_date.is_(None), OrderLine.due_date.asc(), OrderLine.id.asc())
        .all()
    )
    return [
        LineIn(
            key=f"o:{r.id}",
            order_line_id=r.id,
            product_id=r.product_id,
            product_label=r.product_name_raw,
            qty=int(r.qty),
            due=_aware(r.due_date),
            customer=r.customer,
        )
        for r in rows
    ]


def live_stock(db: Session, lines: list[LineIn]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in lines:
        if ln.product_id and ln.skey() not in out:
            out[ln.skey()] = current_stock(db, int(ln.product_id))
    return out


def _urgency_hours(due: datetime | None, now: datetime) -> float | None:
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (due - now).total_seconds() / 3600.0


def _slot(line: LineIn, **kw: Any) -> dict[str, Any]:
    d = {
        "order_line_id": line.order_line_id,
        "line_key": line.key,
        "product_id": line.product_id,
        "mold_id": None,
        "machine_id": None,
        "suggested_start": None,
        "suggested_end": None,
        "qty_planned": 0,
        "reason": None,
        "score": None,
        "meta": None,
    }
    d.update(kw)
    return d


def compute_plan_legacy(ctx: PlanContext, lines: list[LineIn], stock: dict[str, int]) -> dict[str, Any]:
    """Original greedy planner: per-line stock, single best mold×machine slot."""
    now = ctx.now
    machines = ctx.machines
    free_at: dict[int, datetime] = {mid: now for mid in machines}
    slots: list[dict[str, Any]] = []

    for line in lines:
        if not line.product_id:
            slots.append(_slot(line, product_id=None, reason="Ürün eşleşmedi — önce ürün bağlayın"))
            continue
        stk = int(stock.get(line.skey(), 0))
        need = max(0, int(line.qty) - stk)
        if need <= 0:
            slots.append(_slot(line, reason=f"Stok yeterli (stok={stk}, sipariş={line.qty})"))
            continue

        molds = ctx.molds_by_product.get(int(line.product_id), [])
        if not molds:
            slots.append(_slot(line, reason="Bu ürüne bağlı kalıp yok"))
            continue

        remaining = need
        urgency = _urgency_hours(line.due, now)
        # Prefer speed when due < 72h, else regularity
        prefer_speed = urgency is not None and urgency < 72

        candidates: list[tuple[float, Mold, int, dict | None, MoldMachineCapability | None]] = []
        for mold in molds:
            caps = ctx.caps_by_mold.get(int(mold.id), [])
            # If no capabilities defined, allow all non-faulted machines with low score
            if not caps:
                for mid in machines:
                    st = ctx.stats.get((mold.id, mid))
                    score = _score(st, prefer_speed, priority=50)
                    candidates.append((score, mold, mid, st, None))
            else:
                for cap in caps:
                    if cap.machine_id not in machines:
                        continue
                    st = ctx.stats.get((mold.id, cap.machine_id))
                    score = _score(st, prefer_speed, priority=cap.priority)
                    candidates.append((score, mold, cap.machine_id, st, cap))

        candidates.sort(key=lambda t: (-t[0], t[4].priority if t[4] else 99, t[1].id))

        if not candidates:
            slots.append(_slot(line, reason="Uygun makine yok (duruş veya uyumluluk)"))
            continue

        for score, mold, mid, st, cap in candidates:
            if remaining <= 0:
                break
            cavity = effective_cavity(mold)
            cycle_s = None
            if st and st.get("avg_cycle_s"):
                cycle_s = float(st["avg_cycle_s"])
            elif mold.target_cycle_s:
                cycle_s = float(mold.target_cycle_s)
            elif mold.avg_cycle_s and mold.avg_cycle_s > 0:
                cycle_s = float(mold.avg_cycle_s)
            else:
                cycle_s = 30.0
            qty = remaining
            presses = max(1, (qty + cavity - 1) // cavity)
            duration = timedelta(seconds=presses * cycle_s)
            start = free_at.get(mid, now)
            end = start + duration
            free_at[mid] = end
            reason_parts = [
                f"Skor={score:.2f}",
                "hız öncelikli" if prefer_speed else "düzen öncelikli",
            ]
            if cap:
                reason_parts.append(f"öncelik={cap.priority}")
            if st:
                if st.get("run_ratio") is not None:
                    reason_parts.append(f"run_ratio={st['run_ratio']}")
                if st.get("cycle_gap") is not None:
                    reason_parts.append(f"cycle_gap={st['cycle_gap']}")
            slots.append(
                _slot(
                    line,
                    mold_id=mold.id,
                    machine_id=mid,
                    suggested_start=start,
                    suggested_end=end,
                    qty_planned=qty,
                    reason="; ".join(reason_parts),
                    score=score,
                )
            )
            remaining = 0  # single best machine for v1; split later if needed

        if remaining > 0:
            slots.append(_slot(line, reason=f"Kapasite yetersiz; kalan ihtiyaç {remaining}"))

    return {"slots": slots, "lines": [], "warnings": [], "explanations": [], "kpis": {}}


def compute_plan(ctx: PlanContext, lines: list[LineIn], stock: dict[str, int]) -> dict[str, Any]:
    return compute_plan_legacy(ctx, lines, stock)


def write_plan_run(
    db: Session,
    result: dict[str, Any],
    *,
    status: str,
    created_by: str | None,
    note: str | None,
    as_of: datetime | None = None,
    horizon_end: datetime | None = None,
    prefs: dict | None = None,
    inputs: dict | None = None,
) -> PlanRun:
    plan = PlanRun(
        created_by=created_by,
        note=note,
        status=status,
        stale_reason=None,
        as_of=as_of,
        horizon_end=horizon_end,
        prefs_json=json.dumps(prefs, ensure_ascii=False) if prefs is not None else None,
        input_json=json.dumps(inputs, ensure_ascii=False, default=str) if inputs is not None else None,
    )
    has_extras = any(result.get(k) for k in ("lines", "warnings", "explanations", "kpis"))
    if has_extras:
        plan.result_json = json.dumps(
            {k: result.get(k) for k in ("lines", "warnings", "explanations", "kpis", "prefs_summary")},
            ensure_ascii=False,
            default=str,
        )
    db.add(plan)
    db.flush()
    for s in result["slots"]:
        meta = s.get("meta")
        db.add(
            PlanSlot(
                plan_run_id=plan.id,
                order_line_id=s.get("order_line_id"),
                line_key=s.get("line_key"),
                product_id=s.get("product_id"),
                mold_id=s.get("mold_id"),
                machine_id=s.get("machine_id"),
                suggested_start=s.get("suggested_start"),
                suggested_end=s.get("suggested_end"),
                qty_planned=int(s.get("qty_planned") or 0),
                reason=s.get("reason"),
                score=s.get("score"),
                meta_json=json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
            )
        )
    return plan


def build_plan(db: Session, *, created_by: str | None = None, note: str | None = None) -> PlanRun:
    """Live plan: open order_lines + ledger stock → new `active` plan (old ones superseded)."""
    ctx = gather_context(db)
    lines = open_order_lines(db)
    result = compute_plan(ctx, lines, live_stock(db, lines))
    supersede_open_plans(db)
    plan = write_plan_run(db, result, status="active", created_by=created_by, note=note)
    db.commit()
    db.refresh(plan)
    return plan


def _score(st: dict | None, prefer_speed: bool, *, priority: int) -> float:
    """Higher is better."""
    pri = max(0.0, 1.0 - (priority - 1) * 0.05)
    if not st:
        return 0.3 + pri * 0.2
    run_ratio = float(st.get("run_ratio") or 0.0)
    gap = st.get("cycle_gap")
    speed = 1.0 - min(1.0, abs(float(gap))) if gap is not None else 0.5
    if prefer_speed:
        return 0.55 * speed + 0.25 * run_ratio + 0.20 * pri
    return 0.55 * run_ratio + 0.25 * speed + 0.20 * pri
