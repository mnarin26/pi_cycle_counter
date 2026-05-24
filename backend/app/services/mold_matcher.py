"""Global mold suggestions, matching, weighted averages."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Machine, Mold, MoldMachine, json_dumps
from app.services.mold_names import clear_orphan_cycle_mold_labels


WINDOW = 12
BIN_WIDTH = 0.1
LONG_STOP_MOLD_CHANGE_S = 20 * 60
# After long stop: adaptive stability window before mold decision (all machines).
POST_STOP_REF_CYCLES = 10  # compute stdev limit after this many cycles
POST_STOP_STDEV_RATIO = 0.05  # limit ≈ 5% of reference cycle time (10s→0.5, 20s→1, 40s→2)
POST_STOP_STDEV_MIN = 0.25
POST_STOP_WINDOW_TARGET_S = 600.0  # ~10 min of production for stability window
POST_STOP_WINDOW_MIN = 15
POST_STOP_WINDOW_MAX = 40
POST_STOP_MAX_WAIT_FLOOR = 80
POST_STOP_MAX_WAIT_FACTOR = 3
MAX_REPLAY_DAYS = 7
MAX_REPLAY_CYCLES = 20_000
MOLD_REPLAY_EVENT_TYPES = (
    "mold_auto_matched",
    "mold_change_likely",
    "mold_unknown_prompt",
    "mold_suggestion",
    "abnormal_cycle",
)


@dataclass
class PostStopState:
    """Collect cycles after long stop until a stable window is found."""

    prev_mold_id: int | None
    downtime_s: float
    samples: list[tuple[int, float]] = field(default_factory=list)  # (cycle_id, cycle_s)
    stdev_limit: float | None = None
    window_size: int | None = None


# Live orchestrator state — keyed by machine_id (all machines share same logic).
_post_stop_buffers: dict[int, PostStopState] = {}


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _confidence_from_samples(values: list[float]) -> float:
    if len(values) < 3:
        return 0.3
    try:
        st = statistics.pstdev(values)
        return max(0.0, min(1.0, 1.0 - min(st / max(statistics.mean(values), 0.1), 1.0)))
    except statistics.StatisticsError:
        return 0.5


def _cycle_within_mold(cycle_s: float, mold: Mold) -> bool:
    if mold.avg_cycle_s <= 0:
        return False
    tol = max(0.05, float(mold.tolerance_s or 0.35))
    return abs(cycle_s - mold.avg_cycle_s) <= tol


def _find_nearby_mold(
    db: Session,
    machine_id: int,
    cycle_s: float,
    *,
    allow_unnamed: bool = False,
    exclude_mold_id: int | None = None,
) -> Mold | None:
    """Closest mold within tolerance; optionally includes unnamed candidates."""
    q = (
        db.query(Mold)
        .filter(Mold.status.in_(["active", "candidate"]))
        .filter(Mold.avg_cycle_s > 0)
    )
    if not allow_unnamed:
        q = q.filter(Mold.name.is_not(None))
    rows = q.all()
    linked_ids = {
        int(r[0])
        for r in db.query(MoldMachine.mold_id).filter(MoldMachine.machine_id == machine_id).all()
    }
    best: Mold | None = None
    best_score = float("inf")
    for mold in rows:
        if exclude_mold_id is not None and mold.id == exclude_mold_id:
            continue
        if not _cycle_within_mold(cycle_s, mold):
            continue
        delta = abs(cycle_s - mold.avg_cycle_s)
        score = delta - (0.25 if mold.id in linked_ids else 0.0)
        if mold.name:
            score -= 0.05
        if score < best_score:
            best_score = score
            best = mold
    return best


def _find_best_matching_mold(
    db: Session,
    machine_id: int,
    cycle_s: float,
    *,
    exclude_mold_id: int | None = None,
) -> Mold | None:
    """Pick closest registered mold (active/candidate, named) within tolerance."""
    rows = (
        db.query(Mold)
        .filter(Mold.status.in_(["active", "candidate"]))
        .filter(Mold.name.is_not(None))
        .filter(Mold.avg_cycle_s > 0)
        .all()
    )
    linked_ids = {
        int(r[0])
        for r in db.query(MoldMachine.mold_id).filter(MoldMachine.machine_id == machine_id).all()
    }

    best: Mold | None = None
    best_score = float("inf")
    for mold in rows:
        if exclude_mold_id is not None and mold.id == exclude_mold_id:
            continue
        if not _cycle_within_mold(cycle_s, mold):
            continue
        delta = abs(cycle_s - mold.avg_cycle_s)
        # Prefer molds already seen on this machine, then smallest delta.
        score = delta - (0.25 if mold.id in linked_ids else 0.0)
        if score < best_score:
            best_score = score
            best = mold
    return best


def _post_stop_stdev_limit(ref_mean: float, mold: Mold | None = None) -> float:
    if mold is not None:
        if mold.stdev_limit_s is not None and mold.stdev_limit_s > 0:
            return float(mold.stdev_limit_s)
        if mold.avg_cycle_s > 0:
            ref_mean = mold.avg_cycle_s
    return max(POST_STOP_STDEV_MIN, POST_STOP_STDEV_RATIO * ref_mean)


def effective_mold_stdev_limit(mold: Mold) -> float:
    """Stabilite eşiği (sn): özel değer veya ort. sürenin %5'i."""
    return _post_stop_stdev_limit(mold.avg_cycle_s or 0.0, mold)


def _post_stop_window_size(ref_mean: float) -> int:
    if ref_mean <= 0:
        return POST_STOP_WINDOW_MAX
    return max(
        POST_STOP_WINDOW_MIN,
        min(POST_STOP_WINDOW_MAX, round(POST_STOP_WINDOW_TARGET_S / ref_mean)),
    )


def _post_stop_max_wait(window_size: int) -> int:
    return max(POST_STOP_MAX_WAIT_FLOOR, POST_STOP_MAX_WAIT_FACTOR * window_size)


def _post_stop_times(samples: list[tuple[int, float]]) -> list[float]:
    return [s[1] for s in samples]


def _scan_stable_window(
    times: list[float],
    window_size: int,
    stdev_limit: float,
) -> tuple[int, float, float] | None:
    if len(times) < window_size:
        return None
    for start in range(0, len(times) - window_size + 1):
        w = times[start : start + window_size]
        st = statistics.pstdev(w) if len(w) > 1 else 0.0
        if st <= stdev_limit:
            return start, statistics.mean(w), st
    return None


def _best_post_stop_window(
    times: list[float],
    window_size: int,
) -> tuple[int, float, float]:
    if len(times) < window_size:
        w = times
        st = statistics.pstdev(w) if len(w) > 1 else 0.0
        return 0, statistics.mean(w), st
    best_start = 0
    best_mean = statistics.mean(times[:window_size])
    best_st = float("inf")
    for start in range(0, len(times) - window_size + 1):
        w = times[start : start + window_size]
        st = statistics.pstdev(w) if len(w) > 1 else 0.0
        if st < best_st:
            best_st = st
            best_start = start
            best_mean = statistics.mean(w)
    return best_start, best_mean, best_st


def _decide_mold_from_window(
    db: Session,
    machine_id: int,
    decision_times: list[float],
    prev_mold_id: int | None,
    *,
    post_stop: bool = False,
) -> tuple[str, Mold | None, float, float]:
    """
    Decide outcome from a stability window after long stop.
    Returns (outcome, mold_or_none, mean_s, stdev_s).
    """
    if not decision_times:
        return "unstable", None, 0.0, 0.0

    mean_v = statistics.mean(decision_times)
    st = statistics.pstdev(decision_times) if len(decision_times) > 1 else 0.0

    matched = _find_best_matching_mold(db, machine_id, mean_v)
    if matched:
        return "auto_matched", matched, mean_v, st

    if post_stop:
        reuse = _find_nearby_mold(db, machine_id, mean_v, allow_unnamed=True)
        if reuse:
            return "auto_matched", reuse, mean_v, st
        return "suggestion", None, mean_v, st

    if prev_mold_id:
        prev = db.get(Mold, prev_mold_id)
        if prev and prev.avg_cycle_s > 0:
            d = abs(mean_v - prev.avg_cycle_s)
            tol = max(0.05, float(prev.tolerance_s or 0.35))
            if d >= max(0.8, tol * 2.0):
                return "change_likely", None, mean_v, st
            return "unknown_prompt", None, mean_v, st

    return "suggestion", None, mean_v, st


def _mold_display_name(mold: Mold | None) -> str | None:
    if not mold:
        return None
    return mold.name


def _retroactive_assign_post_stop_cycles(
    db: Session,
    samples: list[tuple[int, float]],
    *,
    mold_id: int | None,
    mold_name: str | None,
    is_counted: bool,
    exclude_reason: str | None,
) -> None:
    for cycle_id, _ in samples:
        c = db.get(Cycle, cycle_id)
        if not c:
            continue
        c.mold_id = mold_id
        c.mold_name_snapshot = mold_name
        c.is_counted = is_counted
        c.exclude_reason = exclude_reason


def _resolve_post_stop_window(
    state: PostStopState,
) -> tuple[list[float], int, float, float, float, int, bool] | None:
    """
    Return decision window times and metadata, or None if still collecting.
    Tuple: (decision_times, window_start, mean, stdev, stdev_limit, window_size, forced)
    """
    times = _post_stop_times(state.samples)
    n = len(times)
    if n < POST_STOP_REF_CYCLES:
        return None

    ref_mean = statistics.mean(times)
    stdev_limit = state.stdev_limit if state.stdev_limit is not None else _post_stop_stdev_limit(ref_mean)
    window_size = state.window_size if state.window_size is not None else _post_stop_window_size(ref_mean)
    state.stdev_limit = stdev_limit
    state.window_size = window_size
    max_wait = _post_stop_max_wait(window_size)

    stable = _scan_stable_window(times, window_size, stdev_limit)
    forced = False
    if stable is not None:
        window_start, mean_v, st = stable
    elif n >= max_wait:
        window_start, mean_v, st = _best_post_stop_window(times, window_size)
        forced = True
    else:
        return None

    decision_times = times[window_start : window_start + window_size]
    if len(decision_times) < window_size:
        decision_times = times[-window_size:] if len(times) >= window_size else times
    return decision_times, window_start, mean_v, st, stdev_limit, window_size, forced


def _finalize_post_stop_decision(
    db: Session,
    machine: Machine,
    state: PostStopState,
    rolling: dict[int, list[float]],
    *,
    update_stats: bool,
    event_at: datetime | None,
    decision_times: list[float],
    window_start: int,
    mean_v: float,
    st: float,
    stdev_limit: float,
    window_size: int,
    forced: bool,
) -> tuple[int | None, bool, str | None, int]:
    outcome, matched, mean_v, st = _decide_mold_from_window(
        db, machine.id, decision_times, state.prev_mold_id, post_stop=True
    )
    mid = machine.id
    mold_id: int | None = None
    mold_name: str | None = None
    is_counted = True
    exclude_reason: str | None = None
    ev_type: str | None = None
    retro_count = len(state.samples)
    payload: dict[str, Any] = {
        "downtime_s": round(state.downtime_s, 1),
        "ref_cycles": POST_STOP_REF_CYCLES,
        "window_size": window_size,
        "window_start_index": window_start,
        "cycles_collected": retro_count,
        "stdev_limit": round(stdev_limit, 3),
        "decision_mean_s": round(mean_v, 3),
        "decision_stdev_s": round(st, 3),
        "forced_match": forced,
        "prev_mold_id": state.prev_mold_id,
    }

    if outcome == "auto_matched" and matched:
        mold_id = matched.id
        mold_name = matched.name
        machine.current_mold_id = matched.id
        if update_stats:
            for t in decision_times:
                apply_weighted_average(db, matched, t)
                link_mold_machine(db, matched.id, machine.id)
        ev_type = "mold_auto_matched"
        msg = (
            "Uzun duruş sonrası stabil pencere kayıtlı kalıpla eşleşti; "
            "duruştan beri tüm döngüler geriye atandı."
        )
        if forced:
            msg = (
                "Uzun döngü sınırında en stabil pencere ile zorunlu eşleşme; "
                "duruştan beri tüm döngüler geriye atandı."
            )
        payload.update(
            {
                "message": msg,
                "matched_mold_id": matched.id,
                "matched_mold_name": matched.name,
                "matched_avg_s": matched.avg_cycle_s,
            }
        )
        rolling[mid] = list(decision_times)
    elif outcome == "suggestion":
        tol = max(0.2, 0.05 * mean_v)
        reuse = _find_nearby_mold(db, machine.id, mean_v, allow_unnamed=True)
        if reuse:
            mold_id = reuse.id
            mold_name = reuse.name
            machine.current_mold_id = reuse.id
            if update_stats:
                for t in decision_times:
                    apply_weighted_average(db, reuse, t)
                link_mold_machine(db, reuse.id, machine.id)
            ev_type = "mold_auto_matched" if reuse.name else "mold_suggestion"
            payload.update(
                {
                    "message": (
                        "Uzun duruş sonrası stabil pencere mevcut kalıpla eşleşti; "
                        "duruştan beri tüm döngüler geriye atandı."
                    ),
                    "matched_mold_id": reuse.id,
                    "matched_mold_name": reuse.name,
                    "matched_avg_s": reuse.avg_cycle_s,
                }
            )
            rolling[mid] = list(decision_times)
        else:
            nm = Mold(
                name=None,
                status="candidate",
                avg_cycle_s=mean_v,
                tolerance_s=tol,
                sample_count=len(decision_times),
                confidence=_confidence_from_samples(decision_times),
            )
            db.add(nm)
            db.flush()
            mold_id = nm.id
            mold_name = None
            machine.current_mold_id = nm.id
            if update_stats:
                link_mold_machine(db, nm.id, machine.id)
            ev_type = "mold_suggestion"
            payload.update(
                {
                    "message": (
                        "Uzun duruş sonrası stabil pencereden isimsiz kalıp önerisi; "
                        "duruştan beri tüm döngüler geriye atandı."
                    ),
                    "avg_cycle_s": mean_v,
                    "tolerance_s": tol,
                }
            )
            rolling[mid] = []
    else:
        machine.current_mold_id = None
        is_counted = False
        exclude_reason = "unknown_or_mold_change"
        ev_type = "mold_unknown_prompt"
        payload["message"] = "Uzun duruş sonrası stabil pencere belirsiz; onay gerekli."
        payload["reason"] = "unknown_pattern"
        rolling[mid] = list(decision_times)

    _retroactive_assign_post_stop_cycles(
        db,
        state.samples,
        mold_id=mold_id,
        mold_name=mold_name,
        is_counted=is_counted,
        exclude_reason=exclude_reason,
    )

    if ev_type:
        db.add(
            Event(
                type=ev_type,
                machine_id=mid,
                payload=json_dumps(payload),
                created_at=event_at or datetime.now(timezone.utc),
            )
        )

    db.flush()
    return mold_id, is_counted, exclude_reason, retro_count


def _post_stop_add_cycle(
    db: Session,
    machine: Machine,
    cycle_s: float,
    t_start: datetime,
    t_end: datetime,
    confidence: float,
    state: PostStopState,
    rolling: dict[int, list[float]],
    post_stop_bufs: dict[int, PostStopState],
    *,
    update_stats: bool,
    event_at: datetime | None,
    existing_cycle: Cycle | None = None,
) -> tuple[int | None, bool, str | None, int]:
    """Append one cycle to post-stop buffer; finalize when a stable window is found."""
    if existing_cycle is not None:
        c = existing_cycle
        c.mold_id = None
        c.mold_name_snapshot = None
        c.is_counted = False
        c.exclude_reason = "post_stop_pending"
    else:
        c = Cycle(
            machine_id=machine.id,
            mold_id=None,
            cycle_time_s=cycle_s,
            t_start=t_start,
            t_end=t_end,
            confidence=confidence,
            mold_name_snapshot=None,
            is_counted=False,
            exclude_reason="post_stop_pending",
        )
        db.add(c)
        db.flush()

    state.samples.append((c.id, cycle_s))

    resolved = _resolve_post_stop_window(state)
    if resolved is None:
        return None, False, "post_stop_pending", 0

    decision_times, window_start, mean_v, st, stdev_limit, window_size, forced = resolved
    mold_id, is_counted, exclude_reason, retro_count = _finalize_post_stop_decision(
        db,
        machine,
        state,
        rolling,
        update_stats=update_stats,
        event_at=event_at,
        decision_times=decision_times,
        window_start=window_start,
        mean_v=mean_v,
        st=st,
        stdev_limit=stdev_limit,
        window_size=window_size,
        forced=forced,
    )
    post_stop_bufs.pop(machine.id, None)
    return mold_id, is_counted, exclude_reason, retro_count


def _long_stop_begin_action(
    machine: Machine,
    prev_mold_id: int | None,
    downtime_s: float,
) -> list[dict[str, Any]]:
    return [
        {"type": "set_machine_mold", "mold_id": None},
        {
            "type": "begin_post_stop_decision",
            "prev_mold_id": prev_mold_id,
            "downtime_s": downtime_s,
        },
    ]


def _detect_shift_reason(previous_values: list[float], current_value: float) -> str:
    if len(previous_values) < 4:
        return "insufficient_history"
    base = statistics.mean(previous_values[-4:])
    jump_threshold = max(0.8, base * 0.2)
    if abs(current_value - base) >= jump_threshold:
        return "sudden_change"
    recent = previous_values[-5:]
    if len(recent) >= 5:
        non_decreasing = all(recent[i] <= recent[i + 1] for i in range(len(recent) - 1))
        if non_decreasing and (recent[-1] - recent[0]) >= max(0.5, base * 0.12):
            return "gradual_drift"
    return "unknown_pattern"


def suggest_or_match_cycles(
    db: Session,
    machine: Machine,
    cycle_s: float,
    rolling: dict[int, list[float]],
    *,
    at_time: datetime | None = None,
    prev_t_end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Returns list of side-effect descriptions; caller applies DB changes."""
    actions: list[dict[str, Any]] = []
    mid = machine.id
    downtime_s: float | None = None
    if prev_t_end is not None and at_time is not None:
        downtime_s = (_aware(at_time) - _aware(prev_t_end)).total_seconds()
    else:
        prev_cycle = (
            db.query(Cycle)
            .filter(Cycle.machine_id == mid)
            .order_by(Cycle.t_end.desc())
            .first()
        )
        now_utc = datetime.now(timezone.utc)
        if prev_cycle and prev_cycle.t_end:
            downtime_s = (now_utc - _aware(prev_cycle.t_end)).total_seconds()
    rolling.setdefault(mid, []).append(cycle_s)
    rolling[mid] = rolling[mid][-WINDOW:]

    mold = None
    if machine.current_mold_id:
        mold = db.get(Mold, machine.current_mold_id)
        if mold and mold.status == "ignored":
            mold = None

    if mold and mold.avg_cycle_s > 0 and mold.tolerance_s > 0:
        d = abs(cycle_s - mold.avg_cycle_s)
        if d <= max(0.05, mold.tolerance_s):
            if downtime_s is not None and downtime_s >= LONG_STOP_MOLD_CHANGE_S:
                # Uzun duruş sonrası ayar dönemini atlamak için her zaman 40 döngü penceresi.
                rolling[mid] = []
                return _long_stop_begin_action(machine, mold.id, downtime_s)
            actions.append(
                {
                    "type": "update_mold_weighted",
                    "mold_id": mold.id,
                    "new_sample": cycle_s,
                }
            )
            return actions
        # During normal production flow, do not ask user immediately.
        # Unknown/mold-change decisions are evaluated only after long stop.
        if downtime_s is None or downtime_s < LONG_STOP_MOLD_CHANGE_S:
            return actions

        # Long stop + cycle süresi aktif kalıba uymuyor → 40 döngü penceresi ile karar.
        rolling[mid] = []
        return _long_stop_begin_action(machine, mold.id if mold else None, downtime_s)

    # Aktif kalıp yok; uzun duruş sonrası 40 döngü penceresi ile karar.
    if downtime_s is not None and downtime_s >= LONG_STOP_MOLD_CHANGE_S:
        rolling[mid] = []
        return _long_stop_begin_action(machine, None, downtime_s)

    if len(rolling[mid]) < 5:
        return actions

    mean_v = statistics.mean(rolling[mid])
    st = statistics.pstdev(rolling[mid]) if len(rolling[mid]) > 1 else 0.2
    if st > 0.35:
        return actions

    if machine.current_mold_id:
        cm = db.get(Mold, machine.current_mold_id)
        if cm and cm.status == "candidate" and abs(cm.avg_cycle_s - mean_v) < 0.35:
            return actions

    matched = _find_best_matching_mold(db, mid, mean_v)
    if matched:
        actions.append({"type": "set_machine_mold", "mold_id": matched.id})
        actions.append(
            {
                "type": "update_mold_weighted",
                "mold_id": matched.id,
                "new_sample": mean_v,
            }
        )
        actions.append(
            {
                "type": "event",
                "event": {
                    "type": "mold_auto_matched",
                    "machine_id": mid,
                    "payload": json_dumps(
                        {
                            "message": "Yeni üretim penceresi kayıtlı kalıpla eşleşti.",
                            "matched_mold_id": matched.id,
                            "matched_mold_name": matched.name,
                            "avg_cycle_s": mean_v,
                        }
                    ),
                },
            }
        )
        rolling[mid] = []
        return actions

    tol = max(0.2, 0.05 * mean_v)
    existing = (
        db.query(Mold)
        .filter(Mold.status.in_(["active", "candidate"]))
        .filter(Mold.name.is_(None))
        .all()
    )
    for m in existing:
        if abs(m.avg_cycle_s - mean_v) < BIN_WIDTH * 2 and m.sample_count >= 3:
            return actions

    conf = _confidence_from_samples(rolling[mid])
    actions.append(
        {
            "type": "create_candidate_mold",
            "machine_id": mid,
            "avg_cycle_s": mean_v,
            "tolerance_s": tol,
            "sample_count": len(rolling[mid]),
            "confidence": conf,
        }
    )
    actions.append(
        {
            "type": "event",
            "event": {
                "type": "mold_suggestion",
                "machine_id": mid,
                "payload": json_dumps(
                    {
                        "message": "Unnamed Mold Suggestion",
                        "avg_cycle_s": mean_v,
                        "tolerance_s": tol,
                        "confidence": conf,
                    }
                ),
            },
        }
    )
    return actions


def apply_mold_actions(
    db: Session,
    machine: Machine,
    actions: list[dict[str, Any]],
    rolling: dict[int, list[float]],
    *,
    update_stats: bool = True,
    event_at: datetime | None = None,
) -> tuple[int | None, bool, str | None]:
    """Apply matcher actions. Returns (mold_id, is_counted, exclude_reason)."""
    mold_id = machine.current_mold_id
    is_counted = True
    exclude_reason: str | None = None

    for a in actions:
        if a["type"] == "update_mold_weighted":
            if not update_stats:
                continue
            m = db.get(Mold, a["mold_id"])
            if m:
                apply_weighted_average(db, m, a["new_sample"])
                link_mold_machine(db, m.id, machine.id)
        elif a["type"] == "set_machine_mold":
            machine.current_mold_id = a.get("mold_id")
        elif a["type"] == "create_candidate_mold":
            nm = Mold(
                name=None,
                status="candidate",
                avg_cycle_s=a["avg_cycle_s"],
                tolerance_s=a["tolerance_s"],
                sample_count=a["sample_count"],
                confidence=a["confidence"],
            )
            db.add(nm)
            db.flush()
            machine.current_mold_id = nm.id
            mold_id = nm.id
            if update_stats:
                link_mold_machine(db, nm.id, machine.id)
            rolling[machine.id] = []
        elif a["type"] == "begin_post_stop_decision":
            continue
        elif a["type"] == "event":
            ev = a["event"]
            db.add(
                Event(
                    type=ev["type"],
                    machine_id=ev.get("machine_id"),
                    payload=ev.get("payload"),
                    created_at=event_at or datetime.now(timezone.utc),
                )
            )
            if ev["type"] in {"mold_unknown_prompt", "mold_change_likely"}:
                is_counted = False
                exclude_reason = "unknown_or_mold_change"

    db.flush()
    machine = db.get(Machine, machine.id)
    mold_id = machine.current_mold_id if machine else mold_id
    return mold_id, is_counted, exclude_reason


def replay_mold_history(
    db: Session,
    machine_id: int,
    start: datetime,
    end: datetime,
    mode: str = "missing_only",
) -> dict[str, Any]:
    """
    Walk cycles in [start, end] chronologically and run mold matcher logic.

    mode:
      missing_only — only assign cycles with mold_id=null; keep existing assignments
      reprocess — clear mold fields in window and re-run matcher (overwrite)
    """
    if mode not in {"missing_only", "reprocess"}:
        raise ValueError("mode must be missing_only or reprocess")

    start = _aware(start)
    end = _aware(end)
    if end <= start:
        raise ValueError("Bitiş başlangıçtan sonra olmalı")
    span_days = (end - start).total_seconds() / 86400
    if span_days > MAX_REPLAY_DAYS:
        raise ValueError(f"En fazla {MAX_REPLAY_DAYS} günlük aralık işlenebilir")

    machine = db.get(Machine, machine_id)
    if not machine:
        raise ValueError("Makine bulunamadı")

    clear_orphan_cycle_mold_labels(db, machine_id)

    cycles = (
        db.query(Cycle)
        .filter(Cycle.machine_id == machine_id, Cycle.t_end >= start, Cycle.t_end <= end)
        .order_by(Cycle.t_end.asc())
        .all()
    )
    if len(cycles) > MAX_REPLAY_CYCLES:
        raise ValueError(
            f"Çok fazla cycle ({len(cycles)}); en fazla {MAX_REPLAY_CYCLES} işlenebilir"
        )

    if mode == "reprocess":
        for c in cycles:
            c.mold_id = None
            c.mold_name_snapshot = None
            if c.exclude_reason == "unknown_or_mold_change":
                c.exclude_reason = None
                c.is_counted = True
        db.query(Event).filter(
            Event.machine_id == machine_id,
            Event.type.in_(MOLD_REPLAY_EVENT_TYPES),
            Event.created_at >= start,
            Event.created_at <= end,
        ).delete(synchronize_session=False)
        db.flush()

    before = (
        db.query(Cycle)
        .filter(Cycle.machine_id == machine_id, Cycle.t_end < start)
        .order_by(Cycle.t_end.desc())
        .first()
    )
    if mode == "reprocess":
        machine.current_mold_id = before.mold_id if before else None
    elif before and before.mold_id:
        machine.current_mold_id = before.mold_id
    else:
        machine.current_mold_id = None

    rolling: dict[int, list[float]] = {}
    post_stop_bufs: dict[int, PostStopState] = {}
    prev_t_end: datetime | None = before.t_end if before else None
    if before and before.mold_id and before.cycle_time_s:
        rolling[machine_id] = [before.cycle_time_s]

    assigned = 0
    skipped = 0
    events_created = 0

    for cycle in cycles:
        if mode == "missing_only" and cycle.mold_id is not None:
            mold_row = db.get(Mold, cycle.mold_id)
            if mold_row is not None:
                post_stop_bufs.pop(machine_id, None)
                machine.current_mold_id = cycle.mold_id
                if cycle.mold_id and cycle.cycle_time_s:
                    rolling.setdefault(machine_id, []).append(cycle.cycle_time_s)
                    rolling[machine_id] = rolling[machine_id][-WINDOW:]
                if mold_row.name and cycle.mold_name_snapshot != mold_row.name:
                    cycle.mold_name_snapshot = mold_row.name
                prev_t_end = cycle.t_end
                skipped += 1
                continue
            cycle.mold_id = None
            cycle.mold_name_snapshot = None

        active_ps = post_stop_bufs.get(machine_id)
        if active_ps is not None:
            mold_id, is_counted, exclude_reason, retro_n = _post_stop_add_cycle(
                db,
                machine,
                cycle.cycle_time_s,
                cycle.t_start,
                cycle.t_end,
                float(cycle.confidence or 1.0),
                active_ps,
                rolling,
                post_stop_bufs,
                update_stats=False,
                event_at=cycle.t_end,
                existing_cycle=cycle,
            )
            if exclude_reason != "post_stop_pending":
                events_created += 1
                if mold_id:
                    assigned += retro_n
            prev_t_end = cycle.t_end
            continue

        actions = suggest_or_match_cycles(
            db,
            machine,
            cycle.cycle_time_s,
            rolling,
            at_time=cycle.t_end,
            prev_t_end=prev_t_end,
        )
        begin = next((a for a in actions if a["type"] == "begin_post_stop_decision"), None)
        if begin is not None:
            machine.current_mold_id = None
            post_stop_bufs[machine_id] = PostStopState(
                prev_mold_id=begin.get("prev_mold_id"),
                downtime_s=float(begin.get("downtime_s") or 0),
            )
            mold_id, is_counted, exclude_reason, retro_n = _post_stop_add_cycle(
                db,
                machine,
                cycle.cycle_time_s,
                cycle.t_start,
                cycle.t_end,
                float(cycle.confidence or 1.0),
                post_stop_bufs[machine_id],
                rolling,
                post_stop_bufs,
                update_stats=False,
                event_at=cycle.t_end,
                existing_cycle=cycle,
            )
            if exclude_reason != "post_stop_pending":
                events_created += 1
                if mold_id:
                    assigned += retro_n
            prev_t_end = cycle.t_end
            continue

        mold_id, is_counted, exclude_reason = apply_mold_actions(
            db,
            machine,
            actions,
            rolling,
            update_stats=False,
            event_at=cycle.t_end,
        )
        events_created += sum(1 for a in actions if a["type"] == "event")

        mold = db.get(Mold, mold_id) if mold_id else None
        mold_name = mold.name if mold and mold.name else None

        if cycle.cycle_time_s >= max(1.0, float(machine.no_movement_timeout_s or 120.0)):
            is_counted = False
            exclude_reason = "long_stop_or_no_movement"

        cycle.mold_id = mold_id
        cycle.mold_name_snapshot = mold_name or cycle.mold_name_snapshot
        cycle.is_counted = is_counted
        cycle.exclude_reason = exclude_reason

        if cycle.cycle_time_s > 0 and mold_id:
            m2 = db.get(Mold, mold_id)
            if m2 and m2.avg_cycle_s > 0 and m2.tolerance_s > 0:
                if abs(cycle.cycle_time_s - m2.avg_cycle_s) > max(m2.tolerance_s * 4, 1.5):
                    cycle.is_counted = False
                    cycle.exclude_reason = "abnormal_cycle"
                    db.add(
                        Event(
                            type="abnormal_cycle",
                            machine_id=machine.id,
                            payload=json_dumps(
                                {"cycle_s": cycle.cycle_time_s, "expected": m2.avg_cycle_s}
                            ),
                            created_at=cycle.t_end,
                        )
                    )
                    events_created += 1

        if mold_id:
            assigned += 1
        prev_t_end = cycle.t_end

    db.commit()
    return {
        "machine_id": machine_id,
        "mode": mode,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cycles_total": len(cycles),
        "cycles_assigned": assigned,
        "cycles_skipped_existing": skipped,
        "events_created": events_created,
    }


def apply_weighted_average(db: Session, mold: Mold, sample: float) -> None:
    n = max(1, mold.sample_count)
    new_avg = (n * mold.avg_cycle_s + sample) / (n + 1)
    mold.avg_cycle_s = new_avg
    mold.sample_count = n + 1
    mold.confidence = min(1.0, mold.confidence + 0.02)
    mold.updated_at = datetime.now(timezone.utc)


def link_mold_machine(db: Session, mold_id: int, machine_id: int) -> None:
    row = (
        db.query(MoldMachine)
        .filter_by(mold_id=mold_id, machine_id=machine_id)
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if row:
        row.last_seen_at = now
        row.cycles_attributed = row.cycles_attributed + 1
    else:
        db.add(
            MoldMachine(
                mold_id=mold_id,
                machine_id=machine_id,
                first_seen_at=now,
                last_seen_at=now,
                cycles_attributed=1,
            )
        )


def handle_cycle_completion(
    db: Session,
    machine: Machine,
    cycle_s: float,
    t_start: datetime,
    t_end: datetime,
    rolling: dict[int, list[float]],
    mold_name_snapshot: str | None,
    confidence: float,
    post_stop_bufs: dict[int, PostStopState] | None = None,
) -> None:
    bufs = _post_stop_buffers if post_stop_bufs is None else post_stop_bufs

    active = bufs.get(machine.id)
    if active is not None:
        _post_stop_add_cycle(
            db,
            machine,
            cycle_s,
            t_start,
            t_end,
            confidence,
            active,
            rolling,
            bufs,
            update_stats=True,
            event_at=None,
        )
        db.commit()
        return

    actions = suggest_or_match_cycles(db, machine, cycle_s, rolling)
    begin = next((a for a in actions if a["type"] == "begin_post_stop_decision"), None)
    if begin is not None:
        machine.current_mold_id = None
        bufs[machine.id] = PostStopState(
            prev_mold_id=begin.get("prev_mold_id"),
            downtime_s=float(begin.get("downtime_s") or 0),
        )
        _post_stop_add_cycle(
            db,
            machine,
            cycle_s,
            t_start,
            t_end,
            confidence,
            bufs[machine.id],
            rolling,
            bufs,
            update_stats=True,
            event_at=None,
        )
        db.commit()
        return

    mold_id, is_counted, exclude_reason = apply_mold_actions(
        db, machine, actions, rolling, update_stats=True
    )
    mold = db.get(Mold, mold_id) if mold_id else None
    if mold and mold.name:
        mold_name_snapshot = mold.name

    if cycle_s >= max(1.0, float(machine.no_movement_timeout_s or 120.0)):
        is_counted = False
        exclude_reason = "long_stop_or_no_movement"

    c = Cycle(
        machine_id=machine.id,
        mold_id=mold_id,
        cycle_time_s=cycle_s,
        t_start=t_start,
        t_end=t_end,
        confidence=confidence,
        mold_name_snapshot=mold_name_snapshot,
        is_counted=is_counted,
        exclude_reason=exclude_reason,
    )
    db.add(c)

    if cycle_s > 0 and mold_id:
        m2 = db.get(Mold, mold_id)
        if m2 and m2.avg_cycle_s > 0 and m2.tolerance_s > 0:
            if abs(cycle_s - m2.avg_cycle_s) > max(m2.tolerance_s * 4, 1.5):
                c.is_counted = False
                c.exclude_reason = "abnormal_cycle"
                db.add(
                    Event(
                        type="abnormal_cycle",
                        machine_id=machine.id,
                        payload=json_dumps({"cycle_s": cycle_s, "expected": m2.avg_cycle_s}),
                    )
                )

    db.commit()
