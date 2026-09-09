"""Scenario tests for the single-edge Schmitt cycle counter.

Priority tags (P0 = must pass before going live, P1/P2 = robustness/regression)
mirror the plan's scenario table. Each test asserts on count, cycle_times,
learning events and the final learned closed_ref.
"""

from __future__ import annotations

from app.vision.schmitt_counter import SchmittConfig, SchmittCounter

from synth import Seq, run


def make(**kw) -> SchmittCounter:
    return SchmittCounter(SchmittConfig(**kw))


# --------------------------------------------------------------------------- #
# P0 — core correctness
# --------------------------------------------------------------------------- #

def test_p0_1_normal_production_low_polarity():
    """Every open->closed stroke counts exactly once (0.4-0.8, closed=low)."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    s = Seq().strokes(0.80, 0.40, 5).build()
    r = run(c, s)
    assert r["count"] == 5
    # N fires -> N-1 cycle durations.
    assert len(r["emits"]) == 4
    assert all(dt > 0 for dt in r["emits"])


def test_p0_2_closed_band_jitter_no_double_count():
    """Heavy jitter inside the closed band must not add extra counts."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    b = Seq()
    b.hold(0.80, 3)
    for _ in range(3):
        b.ramp(0.80, 0.40, 6)
        # oscillate between 0.40 and up to 0.48 (< exit 0.50): never re-arms
        b.jitter(0.44, 0.04, 40, seed=1)
        b.ramp(0.44, 0.80, 6)
        b.hold(0.80, 3)
    r = run(c, b.build())
    assert r["count"] == 3


def test_p0_3_open_end_jitter_zero_count():
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    s = Seq().jitter(0.80, 0.05, 200, seed=2).build()
    r = run(c, s)
    assert r["count"] == 0


def test_p0_4_high_polarity():
    """Closed end at the high position value."""
    c = make(closed_polarity="high", closed_ref=0.80, closed_hyst=0.05, learn_enabled=False)
    s = Seq().strokes(0.20, 0.85, 5).build()  # open=0.20, closed=0.85
    r = run(c, s)
    assert r["count"] == 5


def test_p0_4b_manual_mold_long_open_dwell_no_false_count():
    """Manual mold dwells long at the OPEN end; polarity=low must not miscount."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    s = Seq().strokes(0.80, 0.40, 3, open_dwell=60).build()
    r = run(c, s)
    assert r["count"] == 3


def test_p0_5_new_mold_relearn_then_count():
    """Old ref 0.30 never reached by a new mold (min 0.42). After >=10 s of real
    motion, path-2 relearns and counting resumes."""
    c = make(closed_polarity="low", closed_ref=0.30, closed_hyst=0.05)
    # ~27 s of strokes between 0.42 (closed) and 0.80 (open). Relearn fires at
    # ~10 s; the remaining ~9 strokes must then count.
    s = Seq().strokes(0.80, 0.42, 15).build()
    r = run(c, s)
    assert "relearn_path2" in r["learn_kinds"]
    assert r["count"] >= 4
    # Jump limited to 0.10, so ref lands near 0.40 (0.30 + 0.10).
    assert 0.38 <= r["final_ref"] <= 0.43


def test_p0_6_open_end_jog_does_not_move_ref():
    """Jog at the open end (span < stroke_min) must not relearn or count."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05)
    # 20 s of 0.70..0.85 wiggle: span 0.15 < 0.20.
    b = Seq()
    for i in range(20):
        b.ramp(0.70, 0.85, 5)
        b.ramp(0.85, 0.70, 5)
    r = run(c, b.build())
    assert r["count"] == 0
    assert r["learn_kinds"] == []
    assert abs(r["final_ref"] - 0.40) < 1e-6


# --------------------------------------------------------------------------- #
# P1 — learning robustness
# --------------------------------------------------------------------------- #

def test_p1_7_dip_shift_path1_update_count_stable():
    """Dip drifts 0.30->0.40 while still crossing the threshold: count stays
    correct and closed_ref is nudged after 10 cycles."""
    c = make(closed_polarity="low", closed_ref=0.35, closed_hyst=0.08)
    n = 15
    s = Seq().strokes(0.80, 0.40, n).build()
    r = run(c, s)
    assert r["count"] == n
    assert "update_path1" in r["learn_kinds"]
    assert 0.39 <= r["final_ref"] <= 0.41


def test_p1_8_noisy_dips_no_update():
    """Scattered dips (spread > cluster_tol) must not trigger a path-1 update."""
    c = make(closed_polarity="low", closed_ref=0.30, closed_hyst=0.10)
    lows = [0.30, 0.35, 0.38, 0.31, 0.37, 0.33, 0.39, 0.32, 0.36, 0.34,
            0.30, 0.38, 0.33, 0.39, 0.31]
    b = Seq()
    b.hold(0.80, 3)
    for lo in lows:
        b.stroke(0.80, lo)
    r = run(c, b.build())
    assert r["count"] == len(lows)
    assert "update_path1" not in r["learn_kinds"]
    assert abs(r["final_ref"] - 0.30) < 1e-6


def test_p1_9_relearn_needs_min_seconds():
    """Under the 10 s dwell requirement, path-2 must not relearn."""
    c = make(closed_polarity="low", closed_ref=0.30, closed_hyst=0.05,
             no_count_learn_s=10.0)
    # Only ~8 s of motion that never reaches the old closed band.
    b = Seq()
    for _ in range(4):  # 4 strokes ~ 8 s
        b.stroke(0.80, 0.42)
    r = run(c, b.build())
    assert r["count"] == 0
    assert r["learn_kinds"] == []


def test_p1_10_production_with_jog_ref_stays():
    """Real strokes with interleaved open-end jog: ref stays put, count correct."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05)
    b = Seq()
    b.hold(0.80, 3)
    real = 0
    for i in range(8):
        b.stroke(0.80, 0.40)
        real += 1
        # jog at open end between strokes
        b.ramp(0.80, 0.66, 4)
        b.ramp(0.66, 0.80, 4)
    r = run(c, b.build())
    assert r["count"] == real
    assert abs(r["final_ref"] - 0.40) < 1e-6


# --------------------------------------------------------------------------- #
# P2 — edge cases / regressions
# --------------------------------------------------------------------------- #

def test_p2_11_signal_loss_pauses_then_resumes():
    """A long signal loss (orchestrator calls reset) drops the in-progress cycle
    timing but keeps counting afterwards; no bogus cross-gap duration."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    first = Seq().strokes(0.80, 0.40, 3).build()
    r1_emits = []
    for t, pos in first:
        dt = c.step(pos, t)
        if dt:
            r1_emits.append(dt)
    assert c.count == 3
    # Simulate lost signal: orchestrator resets after the grace window.
    c.reset()
    second = Seq(start_t=100.0).strokes(0.80, 0.40, 3).build()
    r2_emits = []
    for t, pos in second:
        dt = c.step(pos, t)
        if dt:
            r2_emits.append(dt)
    assert c.count == 6
    # 3 fires each block -> 2 durations each, none spanning the 100 s gap.
    assert len(r1_emits) == 2
    assert len(r2_emits) == 2
    assert all(dt < 30.0 for dt in r1_emits + r2_emits)


def test_p2_12_jump_limit_single_step():
    """A large dip drift is clamped to max_jump (0.10) in a single update."""
    c = make(
        closed_polarity="low",
        closed_ref=0.35,
        closed_hyst=0.25,
        max_jump=0.10,
        stroke_thresholds=False,
    )
    # closed=0.55 crosses enter (0.60); open=0.95 re-arms above exit (0.85).
    s = Seq().strokes(0.95, 0.55, 12).build()
    r = run(c, s)
    assert "update_path1" in r["learn_kinds"]
    # 0.35 + 0.10 exactly, not 0.55.
    assert abs(r["final_ref"] - 0.45) < 1e-6


def test_p2_13_single_stroke_one_pulse_regression():
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05, learn_enabled=False)
    b = Seq()
    b.hold(0.80, 3)
    b.ramp(0.80, 0.40, 6)
    b.jitter(0.41, 0.02, 20, seed=7)  # micro jitter at closed end
    b.ramp(0.41, 0.80, 6)
    b.hold(0.80, 3)
    r = run(c, b.build())
    assert r["count"] == 1
    assert r["emits"] == []  # only one fire -> no duration yet


def test_p0_opening_stroke_no_double_count_af4():
    """Opening phase crosses old exit (ref+2*hyst) but must not re-arm early."""
    c = make(
        closed_polarity="low",
        closed_ref=0.309,
        closed_hyst=0.08,
        learn_enabled=False,
        stroke_thresholds=True,
    )
    b = Seq()
    for _ in range(6):
        b.hold(0.80, 4)
        b.ramp(0.80, 0.30, 8)
        b.jitter(0.32, 0.03, 12, seed=3)
        b.ramp(0.32, 0.80, 10)
    r = run(c, b.build())
    assert r["count"] == 6


def test_adaptive_min_cycle_rejects_same_stroke_double():
    """Adaptive min_cycle tracks ~18 s strokes and rejects ~10 s doubles."""
    c = make(
        closed_polarity="low",
        closed_ref=0.40,
        closed_hyst=0.05,
        learn_enabled=False,
        min_cycle_s=3.0,
        adaptive_min_cycle=True,
    )
    b = Seq()
    for _ in range(4):
        b.stroke(0.80, 0.40, ramp=6, closed_dwell=80, open_dwell=80)
    r = run(c, b.build())
    assert r["count"] == 4
    assert all(dt >= 14 for dt in r["emits"])


def test_smoothing_kills_single_sample_spike():
    """A one-frame spike into the closed band is removed by median smoothing and
    must not create a count."""
    c = make(closed_polarity="low", closed_ref=0.40, closed_hyst=0.05,
             smooth_win=5, learn_enabled=False)
    b = Seq()
    b.hold(0.80, 10)
    b.hold(0.30, 1)  # single-frame glitch toward closed
    b.hold(0.80, 10)
    r = run(c, b.build())
    assert r["count"] == 0
