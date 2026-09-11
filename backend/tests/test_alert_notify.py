"""Diagnosis Telegram alerts: prefs, window, debounce, no vision-path I/O."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.alert_notify import (
    format_detail_message,
    format_headline,
    in_alert_window,
    operator_receives_alerts,
    recipients_for_alert,
)
from app.services.fault_codes import FAULT_ALERT_MIN_INTERVAL_S
from app.services.stored_settings import (
    ALERT_PERMISSION_KEY,
    _coerce_perms,
    coerce_alert_prefs,
)


def test_admin_does_not_auto_get_alert_messages():
    perms = _coerce_perms("admin", None)
    assert perms["panel_8000"] is True
    assert perms["bot_mold_assign"] is True
    assert perms[ALERT_PERMISSION_KEY] is False


def test_user_alert_messages_default_off():
    perms = _coerce_perms("user", {"panel_8000": True})
    assert perms["panel_8000"] is True
    assert perms[ALERT_PERMISSION_KEY] is False
    perms_on = _coerce_perms("user", {ALERT_PERMISSION_KEY: True})
    assert perms_on[ALERT_PERMISSION_KEY] is True


def test_alert_prefs_defaults():
    p = coerce_alert_prefs(None)
    assert p["include_details"] is False
    assert p["time_start"] == "08:00"
    assert p["time_end"] == "18:00"
    assert p["weekdays"] == [0, 1, 2, 3, 4, 5, 6]


def test_alert_prefs_empty_weekdays_stay_empty():
    p = coerce_alert_prefs({"weekdays": [], "include_details": True, "time_start": "09.00"})
    assert p["weekdays"] == []
    assert p["include_details"] is True
    assert p["time_start"] == "09:00"


def test_in_alert_window_hours_and_days():
    tz = ZoneInfo("Europe/Istanbul")
    prefs = {
        "time_start": "08:00",
        "time_end": "18:00",
        "weekdays": [0, 1, 2, 3, 4],  # Mon–Fri
    }
    monday_noon = datetime(2026, 9, 7, 12, 0, tzinfo=tz)  # Monday
    monday_night = datetime(2026, 9, 7, 19, 0, tzinfo=tz)
    saturday_noon = datetime(2026, 9, 12, 12, 0, tzinfo=tz)
    assert in_alert_window(prefs, now=monday_noon)
    assert not in_alert_window(prefs, now=monday_night)
    assert not in_alert_window(prefs, now=saturday_noon)


def test_in_alert_window_overnight():
    tz = ZoneInfo("Europe/Istanbul")
    prefs = {"time_start": "22:00", "time_end": "06:00", "weekdays": [0, 1, 2, 3, 4, 5, 6]}
    assert in_alert_window(prefs, now=datetime(2026, 9, 7, 23, 0, tzinfo=tz))
    assert in_alert_window(prefs, now=datetime(2026, 9, 7, 5, 0, tzinfo=tz))
    assert not in_alert_window(prefs, now=datetime(2026, 9, 7, 12, 0, tzinfo=tz))


def test_headline_format():
    assert format_headline("AF-4", "Reflektör bulunamadı") == "AF-4 Reflektör bulunamadı"
    assert format_headline("", "Reflektör bulunamadı", machine_id=3) == "Makine 3 Reflektör bulunamadı"


def test_detail_message_separate_fields():
    text = format_detail_message(
        "E001",
        {
            "threshold_mode": "fixed",
            "threshold_active": 40,
            "threshold_offset": 0,
            "peak": 12,
            "background": 8,
            "delta": 4,
            "segment_len": 3,
            "len_min": 5,
            "len_max": 40,
            "line_thickness": 7,
        },
    )
    assert text is not None
    assert "Peak / bg: 12 / 8" in text
    assert "Δ: 4" in text
    assert "Len: 3" in text
    assert "Öneri:" in text


def test_recipients_filter_by_perm_and_telegram_id():
    tz = ZoneInfo("Europe/Istanbul")
    now = datetime(2026, 9, 7, 10, 0, tzinfo=tz)
    cfg = {
        "operators": [
            {
                "id": "1",
                "telegram_user_id": "111",
                "name": "Ali",
                "role": "user",
                "permissions": {"alert_messages": True},
                "alert_prefs": {
                    "include_details": True,
                    "time_start": "08:00",
                    "time_end": "18:00",
                    "weekdays": [0, 1, 2, 3, 4, 5, 6],
                },
            },
            {
                "id": "2",
                "telegram_user_id": "222",
                "name": "Admin",
                "role": "admin",
                "permissions": {"alert_messages": False},
            },
            {
                "id": "local_1",
                "telegram_user_id": "",
                "name": "NoTg",
                "role": "user",
                "permissions": {"alert_messages": True},
            },
        ]
    }
    recs = recipients_for_alert(cfg, now=now)
    assert [r["telegram_user_id"] for r in recs] == ["111"]
    assert recs[0]["include_details"] is True
    assert operator_receives_alerts(cfg["operators"][1], now=now) is False


def test_note_active_enqueues_once_and_debounces(tmp_path, monkeypatch):
    import app.services.fault_log as fl

    monkeypatch.setattr(fl, "_db_path", lambda: tmp_path / "diagnosis.db")
    fl.reset_runtime_for_tests()

    detail = {"peak": 1, "background": 0, "delta": 1}
    assert fl.note_active(machine_id=4, machine_name="AF-4", code="E001", detail=detail, now_mono=1.0)
    pending = fl.list_pending_alerts()
    assert len(pending) == 1
    assert pending[0]["machine_name"] == "AF-4"
    assert pending[0]["code"] == "E001"
    assert pending[0]["title_tr"]

    # Same active alarm, still inside sample interval: memory fast path, no extra queue.
    assert fl.note_active(machine_id=4, machine_name="AF-4", code="E001", detail=detail, now_mono=2.0) is False
    assert len(fl.list_pending_alerts()) == 1

    # Flap: resolve then reopen immediately → new alarm row, but no second Telegram enqueue.
    assert fl.resolve(machine_id=4, code="E001")
    assert fl.note_active(machine_id=4, machine_name="AF-4", code="E001", detail=detail, now_mono=3.0)
    assert len(fl.list_pending_alerts()) == 1

    # After the 30-minute cooldown, a new activation enqueues again.
    key = (4, "E001")
    fl._last_alert_sent_ts[key] = fl.time.time() - (FAULT_ALERT_MIN_INTERVAL_S + 5)
    assert fl.resolve(machine_id=4, code="E001")
    assert fl.note_active(machine_id=4, machine_name="AF-4", code="E001", detail=detail, now_mono=4.0)
    assert len(fl.list_pending_alerts()) == 2

    ids = [p["id"] for p in fl.list_pending_alerts()]
    assert fl.mark_alerts_done(ids) == 2
    assert fl.list_pending_alerts() == []

    fl.reset_runtime_for_tests()


def test_dispatch_sends_headline_then_detail(tmp_path, monkeypatch):
    import app.services.fault_log as fl
    from app.bot import alerts as al

    monkeypatch.setattr(fl, "_db_path", lambda: tmp_path / "diagnosis.db")
    fl.reset_runtime_for_tests()
    fl.note_active(
        machine_id=4,
        machine_name="AF-4",
        code="E001",
        detail={"peak": 10, "background": 2, "delta": 8, "threshold_active": 40},
        now_mono=1.0,
    )
    sent: list[tuple[int, str]] = []

    def fake_send(token, chat_id, text, **kwargs):
        sent.append((int(chat_id), text))

    class _DummyDb:
        def close(self):
            return None

    cfg = {
        "enabled": True,
        "bot_token": "tok",
        "operators": [
            {
                "id": "111",
                "telegram_user_id": "111",
                "name": "Ali",
                "role": "user",
                "permissions": {"alert_messages": True},
                "alert_prefs": {
                    "include_details": True,
                    "time_start": "00:00",
                    "time_end": "23:59",
                    "weekdays": [0, 1, 2, 3, 4, 5, 6],
                },
            }
        ],
    }
    monkeypatch.setattr(al.db_session, "SessionLocal", lambda: _DummyDb())
    monkeypatch.setattr(al, "get_telegram_config", lambda db: cfg)

    assert al.dispatch_pending_alerts(fake_send) == 1
    assert sent[0] == (111, "AF-4 Reflektör bulunamadı")
    assert len(sent) == 2
    assert "Peak / bg" in sent[1][1]
    assert fl.list_pending_alerts() == []
    fl.reset_runtime_for_tests()
