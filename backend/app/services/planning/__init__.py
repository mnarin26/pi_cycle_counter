"""Production planning: products, stock, orders, mold runs, plan suggestions.

Kept off the vision hot path — only used by API / bot assign flows.
"""

from __future__ import annotations

from app.db.models import Mold


def effective_cavity(mold: Mold) -> int:
    if mold.active_cavity_count is not None and int(mold.active_cavity_count) > 0:
        return int(mold.active_cavity_count)
    return max(1, int(mold.cavity_count or 1))
