"""Plan suggestion engine — recommendation only, never auto-assigns."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from app.services.planning.engine import mold_machine_stats, open_fault_machine_ids
from app.services.planning.stock import current_stock


def _urgency_hours(due: datetime | None, now: datetime) -> float | None:
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (due - now).total_seconds() / 3600.0


def build_plan(db: Session, *, created_by: str | None = None, note: str | None = None) -> PlanRun:
    now = datetime.now(timezone.utc)
    open_orders = (
        db.query(OrderLine)
        .filter(OrderLine.status == "open")
        .order_by(OrderLine.due_date.is_(None), OrderLine.due_date.asc(), OrderLine.id.asc())
        .all()
    )
    stats = {
        (s["mold_id"], s["machine_id"]): s for s in mold_machine_stats(db, days=45)
    }
    faulted = open_fault_machine_ids(db)
    machines = {
        m.id: m
        for m in db.query(Machine).filter(Machine.enabled.is_(True)).all()
        if m.id not in faulted
    }
    # machine free-at timeline for packing slots
    free_at: dict[int, datetime] = {mid: now for mid in machines}

    plan = PlanRun(created_by=created_by, note=note)
    db.add(plan)
    db.flush()

    for line in open_orders:
        if not line.product_id:
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=None,
                    qty_planned=0,
                    reason="Ürün eşleşmedi — önce ürün bağlayın",
                )
            )
            continue
        stock = current_stock(db, int(line.product_id))
        need = max(0, int(line.qty) - stock)
        if need <= 0:
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=line.product_id,
                    qty_planned=0,
                    reason=f"Stok yeterli (stok={stock}, sipariş={line.qty})",
                )
            )
            continue

        molds = (
            db.query(Mold)
            .filter(Mold.product_id == line.product_id, Mold.status != "ignored")
            .all()
        )
        if not molds:
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=line.product_id,
                    qty_planned=0,
                    reason="Bu ürüne bağlı kalıp yok",
                )
            )
            continue

        remaining = need
        urgency = _urgency_hours(line.due_date, now)
        # Prefer speed when due < 72h, else regularity
        prefer_speed = urgency is not None and urgency < 72

        candidates: list[tuple[float, Mold, int, dict | None, MoldMachineCapability | None]] = []
        for mold in molds:
            caps = (
                db.query(MoldMachineCapability)
                .filter(
                    MoldMachineCapability.mold_id == mold.id,
                    MoldMachineCapability.allowed.is_(True),
                )
                .order_by(MoldMachineCapability.priority.asc())
                .all()
            )
            # If no capabilities defined, allow all non-faulted machines with low score
            if not caps:
                for mid in machines:
                    st = stats.get((mold.id, mid))
                    score = _score(st, prefer_speed, priority=50)
                    candidates.append((score, mold, mid, st, None))
            else:
                for cap in caps:
                    if cap.machine_id not in machines:
                        continue
                    st = stats.get((mold.id, cap.machine_id))
                    score = _score(st, prefer_speed, priority=cap.priority)
                    candidates.append((score, mold, cap.machine_id, st, cap))

        candidates.sort(key=lambda t: (-t[0], t[4].priority if t[4] else 99, t[1].id))

        if not candidates:
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=line.product_id,
                    qty_planned=0,
                    reason="Uygun makine yok (arıza veya uyumluluk)",
                )
            )
            continue

        # Assign remaining need across top candidates (split by mold capacity)
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
            # Take up to remaining; one slot per machine for this line
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
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=line.product_id,
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
            db.add(
                PlanSlot(
                    plan_run_id=plan.id,
                    order_line_id=line.id,
                    product_id=line.product_id,
                    qty_planned=0,
                    reason=f"Kapasite yetersiz; kalan ihtiyaç {remaining}",
                )
            )

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
