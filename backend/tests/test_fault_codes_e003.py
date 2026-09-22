"""Unit tests for E003 dual-regime len-band fault tips and catalog."""

from __future__ import annotations

from app.services.fault_codes import FAULT_CATALOG, get_fault, suggest_for_code


def test_e003_in_catalog():
    f = get_fault("E003")
    assert f is not None
    assert f["name"] == "DUAL_REGIME_LEN_REJECT"
    assert "E003" in FAULT_CATALOG
    assert "len" in (f["title_tr"] or "").lower() or "uzunluk" in (f["title_tr"] or "").lower() or "küme" in (
        f["title_tr"] or ""
    ).lower()


def test_e003_tips_len_max_overflow():
    tips = suggest_for_code(
        "E003",
        {
            "segment_len": 6,
            "len_min": 1,
            "len_max": 3,
            "detect_regime": "local",
            "reject_reason": "len_band",
        },
    )
    joined = " ".join(tips)
    assert "len_max" in joined or "6" in joined
    assert "E001" in joined  # explicitly distinguishes from classic loss
    assert any("local" in t for t in tips)


def test_e003_tips_len_min_underflow():
    tips = suggest_for_code(
        "E003",
        {"segment_len": 1, "len_min": 3, "len_max": 5, "detect_regime": "local"},
    )
    joined = " ".join(tips)
    assert "len_min" in joined or "1" in joined


def test_e001_unchanged_for_generic_loss():
    tips = suggest_for_code(
        "E001",
        {"segment_len": 0, "len_min": 1, "len_max": 5, "delta": 10, "threshold_active": 20},
    )
    assert tips
    assert any("Reflektör" in t or "eşik" in t.lower() or "Δ" in t for t in tips)
