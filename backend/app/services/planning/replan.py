"""Backtest replan suggestions — "had we reacted at time D, where would the rest finish?"

Suggestion only: nothing is assigned or written. Each suggestion is computed
with a context as of D (the moment the deviation became visible), so it
never uses information from after D.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import MachineDowntime, MoldRun, PlanRun, PlanSlot
from app.services.planning.plan_a2 import Planner, State, deadline_of, fmt_day, fmt_dt, fmt_qty
from app.services.planning.plan_engine import gather_context
from app.services.planning.prefs import PlanPrefs, prefs_from_dict

MAX_SUGGESTIONS = 12


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dt(v: Any) -> datetime | None:
    if v is None:
        return None
    return _aware(v if isinstance(v, datetime) else datetime.fromisoformat(v))


def _deviation_moment(db: Session, slots: list[PlanSlot], row: dict, window_end: datetime) -> tuple[datetime, str] | None:
    """Earliest point where the plan visibly went off track."""
    found: list[tuple[datetime, str]] = []
    for s in slots:
        a, b = _aware(s.suggested_start), _aware(s.suggested_end)
        downs = (
            db.query(MachineDowntime)
            .filter(MachineDowntime.machine_id == s.machine_id, MachineDowntime.start_at < b)
            .all()
        )
        for d in downs:
            ds = _aware(d.start_at)
            if (d.end_at is None or _aware(d.end_at) > a) and ds < window_end:
                found.append((max(ds, a), f"planlanan makine duruşa geçti ({d.note or 'duruş'})"))
    eta = _dt(row["plan"].get("eta"))
    if eta and row["actual"]["status"] in ("late", "open") and eta < window_end:
        found.append((eta, "planlanan bitişte üretim tamamlanmamıştı"))
    return min(found, key=lambda x: x[0]) if found else None


def _produced_by(db: Session, product_id: int, t0: datetime, at: datetime) -> int:
    from app.services.planning.compare import _actual_events

    events, _, _ = _actual_events(db, product_id, t0, at)
    return sum(q for _, q, _, _ in events)


def suggest_replans(db: Session, plan: PlanRun, compare: dict) -> list[dict]:
    t0 = _aware(plan.as_of)
    window_end = _dt(compare["window_end"])
    prefs: PlanPrefs = prefs_from_dict(json.loads(plan.prefs_json)) if plan.prefs_json else PlanPrefs()
    prefs.start_at = None
    slots = db.query(PlanSlot).filter(PlanSlot.plan_run_id == plan.id, PlanSlot.machine_id.isnot(None)).all()
    by_line: dict[str, list[PlanSlot]] = {}
    for s in slots:
        meta = json.loads(s.meta_json) if s.meta_json else {}
        for k in meta.get("line_keys") or [s.line_key]:
            by_line.setdefault(k, []).append(s)

    out: list[dict] = []
    ctx_cache: dict[datetime, Any] = {}
    seen_products: set[int] = set()
    for row in compare["rows"]:
        if len(out) >= MAX_SUGGESTIONS:
            break
        pid = row.get("product_id")
        if not pid or row["risk"]["level"] not in ("late", "risky") or row["actual"]["status"] not in ("late", "open"):
            continue
        if pid in seen_products:
            continue
        line_slots = by_line.get(row["key"], [])
        if not line_slots:
            continue
        dev = _deviation_moment(db, line_slots, row, window_end)
        if not dev:
            continue
        at, why = dev
        if at.second or at.microsecond:
            at = at.replace(second=0, microsecond=0) + timedelta(minutes=1)
        produced = _produced_by(db, int(pid), t0, at)
        done_before = sum(
            int(r["need"]) for r in compare["rows"]
            if r.get("product_id") == pid and r["deadline"] and row["deadline"] and r["deadline"] < row["deadline"]
        )
        remaining = int(row["need"]) - max(0, produced - done_before)
        if remaining <= 0:
            continue
        if at not in ctx_cache:
            ctx_cache[at] = gather_context(db, as_of=at)
        ctx = ctx_cache[at]
        planner = Planner(ctx, prefs, at)
        busy = {
            int(r.machine_id)
            for r in db.query(MoldRun).filter(MoldRun.started_at <= at).all()
            if (r.ended_at is None or _aware(r.ended_at) > at)
        }
        state = State(
            machine_free={mid: at for mid in planner.machines},
            mold_free={},
            mounted=dict(ctx.current_mold),
        )
        deadline = deadline_of(_dt(row["deadline"])) if row["deadline"] else None
        planned_mids = {int(s.machine_id) for s in line_slots}
        best = None
        for mold in planner.molds_for(int(pid)):
            for mid, cap in planner.pairs(mold):
                if mid in busy and ctx.current_mold.get(mid) not in {m.id for m in planner.molds_for(int(pid))}:
                    continue
                o = planner.place(state, mold, mid, cap, remaining, deadline)
                if o and (best is None or o.end < best.end):
                    best = o
        if best is None:
            continue
        actual_finish = _dt(row["actual"]["finish"])
        mname = ctx.machine_names.get(best.mid, f"#{best.mid}")
        kname = ctx.mold_names.get(int(best.mold.id), "")
        same = best.mid in planned_mids
        text = (
            f"{fmt_dt(at)}: {why}. Kalan {fmt_qty(remaining)} adet "
            f"{'aynı makinede' if same else mname + ' makinesinde'} ({kname} kalıbı) üretilseydi "
            f"bitiş {fmt_dt(best.end)} olurdu"
        )
        if deadline:
            text += f" (termin {fmt_day(deadline)}" + (" — yetişirdi" if best.end <= deadline else " — yine geç") + ")"
        if actual_finish:
            gain_h = (actual_finish - best.end).total_seconds() / 3600.0
            if gain_h > 1:
                text += f"; gerçek bitiş {fmt_dt(actual_finish)}, yani yaklaşık {gain_h:.0f} saat önce biterdi"
        text += "."
        out.append(
            {
                "key": row["key"],
                "product_label": row["product_label"],
                "at": at.isoformat(),
                "trigger": why,
                "remaining": remaining,
                "machine_id": best.mid,
                "machine_name": mname,
                "mold_id": int(best.mold.id),
                "mold_name": kname,
                "finish": best.end.isoformat(),
                "meets_due": bool(deadline and best.end <= deadline),
                "text": text,
            }
        )
        seen_products.add(pid)
    return out
