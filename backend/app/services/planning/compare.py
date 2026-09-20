"""Compare plan slots vs actual mold assignments / runs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Event, MachineDowntime, MoldRun, PlanRun, PlanSlot
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
