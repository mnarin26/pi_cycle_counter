"""Telegram long-poll helpers: webhook vs getUpdates conflict detection."""

from __future__ import annotations

import httpx


def http_error_description(exc: httpx.HTTPStatusError) -> str:
    """Best-effort Telegram error description from an HTTPStatusError."""
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            return str(body.get("description") or "")
    except Exception:
        pass
    return (exc.response.text or "")[:500]


def is_webhook_conflict(exc: httpx.HTTPStatusError) -> bool:
    """True when getUpdates failed because a webhook is still registered."""
    if exc.response.status_code != 409:
        return False
    desc = http_error_description(exc).casefold()
    return "webhook" in desc
