"""Production efficiency and target-vs-actual helpers."""

from __future__ import annotations


def pct(actual: float, target: float, *, cap: float | None = 100.0) -> float:
    if target <= 0:
        return 0.0
    value = 100.0 * actual / target
    if cap is not None:
        return round(min(cap, value), 1)
    return round(value, 1)


def performance_efficiency_pct(target_cycle_s: float | None, avg_cycle_s: float | None) -> float:
    """Higher is better when actual cycle time is close to or better than target."""
    if not target_cycle_s or target_cycle_s <= 0:
        return 0.0
    if not avg_cycle_s or avg_cycle_s <= 0:
        return 0.0
    return pct(target_cycle_s, avg_cycle_s)


def realization_pct(actual_count: int, target_count: int) -> float:
    return pct(float(actual_count), float(target_count))


def overall_efficiency_pct(realization: float, performance: float) -> float:
    if realization <= 0 and performance <= 0:
        return 0.0
    if realization <= 0:
        return performance
    if performance <= 0:
        return realization
    return round((realization + performance) / 2.0, 1)


def theoretical_target_from_cycle(shift_hours: float, target_cycle_s: float | None) -> int:
    if not target_cycle_s or target_cycle_s <= 0 or shift_hours <= 0:
        return 0
    return max(0, int((shift_hours * 3600.0) / target_cycle_s))
