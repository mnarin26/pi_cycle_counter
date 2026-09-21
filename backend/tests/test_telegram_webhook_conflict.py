"""Unit tests for Telegram getUpdates / webhook conflict helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from app.bot.telegram_poll import http_error_description, is_webhook_conflict


def _status_error(status: int, *, json_body: dict | None = None, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.telegram.org/botX/getUpdates")
    if json_body is not None:
        response = httpx.Response(status, json=json_body, request=request)
    else:
        response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_is_webhook_conflict_detects_telegram_webhook_409() -> None:
    exc = _status_error(
        409,
        json_body={
            "ok": False,
            "error_code": 409,
            "description": (
                "Conflict: can't use getUpdates method while webhook is active; "
                "use deleteWebhook to delete the webhook first"
            ),
        },
    )
    assert is_webhook_conflict(exc) is True
    assert "webhook" in http_error_description(exc).casefold()


def test_is_webhook_conflict_false_for_other_getupdates_instance() -> None:
    exc = _status_error(
        409,
        json_body={
            "ok": False,
            "error_code": 409,
            "description": (
                "Conflict: terminated by other getUpdates request; "
                "make sure that only one bot instance is running"
            ),
        },
    )
    assert is_webhook_conflict(exc) is False


def test_is_webhook_conflict_false_for_non_409() -> None:
    exc = _status_error(401, json_body={"ok": False, "description": "Unauthorized"})
    assert is_webhook_conflict(exc) is False


def test_http_error_description_falls_back_to_text() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/botX/getUpdates")
    response = httpx.Response(409, text="Conflict: webhook is active", request=request)
    response.json = MagicMock(side_effect=ValueError("no json"))  # type: ignore[method-assign]
    exc = httpx.HTTPStatusError("err", request=request, response=response)
    assert "webhook" in http_error_description(exc).casefold()
    assert is_webhook_conflict(exc) is True
