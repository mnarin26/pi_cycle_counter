"""Single-edge (Schmitt-trigger) cycle counter with user-selected polarity and
conservative auto-learning of the closed reference level.

Why this exists
---------------
The peak/trough state machine (``state_machine.py``) counts a swing only after
the signal retraces ``min_prominence`` away from an extreme. When a machine
parks at its closed end with small jitter, the retrace never happens, so
consecutive strokes merge (2x/3x cycles). This module instead treats counting
like a digital pressure switch:

- The user fixes the **polarity**: ``closed_polarity='low'`` means the closed
  end sits at low position values; ``'high'`` means the closed end is at high
  values. Polarity is *not* inferred from dwell time, because some manual molds
  also dwell long at the open end.
- A single hysteresis band near the closed end. Each time the (smoothed) signal
  enters the closed band, we count exactly one cycle (a rising edge into
  "closed"). Re-arm requires travelling a **stroke-proportional** distance into
  the open zone (like a spring-return inductive sensor that only rearms after
  the plunger fully retracts), so opening-phase crossings cannot double-count.
- ``min_cycle_s`` is adapted from recent cycle durations so short and long molds
  alike reject false doubles without per-mold tuning.
- ``closed_ref`` (the closed-end level) is learned from recent cycle extremes so
  a mold change that shifts the dip is tolerated, with guards against learning
  from open-end jog.

Everything works in a normalized *x* space where **small x == more closed**:

    x = pos            if polarity == 'low'
    x = 1 - pos        if polarity == 'high'

so the counting logic is identical for both polarities.

The public interface mirrors ``CycleTracker.on_confirmed``: ``step(pos, now_s)``
returns the cycle duration in seconds when a cycle completes, else ``None``.
Time is injected (``now_s``) so replay/tests are fully deterministic.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SchmittConfig:
    # 'low' -> closed end at low pos; 'high' -> closed end at high pos.
    closed_polarity: str = "low"
    # Closed level in pos-space (0..1). None -> learn on warmup.
    closed_ref: float | None = None
    # Hysteresis half-width (in normalized units). Enter closed band at ref+hyst.
    closed_hyst: float = 0.05
    # Re-arm only after travelling this fraction of the observed stroke span
    # away from the closed end (inductive-sensor / full-retract model).
    stroke_thresholds: bool = True
    rearm_stroke_frac: float = 0.68
    # Adaptive debounce: reject fires closer than ratio * median(recent cycles).
    adaptive_min_cycle: bool = True
    adaptive_min_cycle_ratio: float = 0.58
    adaptive_min_cycle_floor_s: float = 3.0
    # Median smoothing window on raw pos (odd preferred).
    smooth_win: int = 5
    learn_enabled: bool = True

    # --- learning knobs (defaults tuned for real strokes; tests may override) ---
    stroke_min: float = 0.20          # min open-closed span to accept as production
    cluster_tol: float = 0.04         # last-N dips must cluster within this to update
    max_jump: float = 0.10            # closed_ref may move at most this per update
    learn_every: int = 10             # cycles between path-1 updates
    warn_every: int = 5               # cycles before a drift warning
    no_count_learn_s: float = 10.0    # min motion time before path-2 relearn
    cooldown_s: float = 1800.0        # min seconds between learning updates
    # A dip is "off" (drifted) when it never gets within this of closed_ref.
    drift_tol: float = 0.04
    # Ignore closed-band re-entries closer than this (false doubles from jitter).
    # 0 disables. Production default comes from vision_settings (~10 s).
    min_cycle_s: float = 0.0


@dataclass
class LearnEvent:
    kind: str            # 'update_path1' | 'relearn_path2' | 'drift_warn'
    old_ref: float | None
    new_ref: float | None
    now_s: float
    detail: str = ""


@dataclass
class SchmittCounter:
    cfg: SchmittConfig = field(default_factory=SchmittConfig)

    # public/observable
    count: int = 0
    last_learn: LearnEvent | None = None
    learn_events: list[LearnEvent] = field(default_factory=list)

    # smoothing
    _win: deque[float] = field(default_factory=deque)

    # counting state (x-space; small == closed)
    _xref: float | None = None
    _armed: bool = False
    _in_closed: bool = False
    _t_last_fire: float | None = None

    # per-cycle extremes (x-space)
    _cyc_min_x: float | None = None
    _cyc_max_x: float | None = None

    # warmup (before closed_ref known)
    _warm_min_x: float | None = None
    _warm_max_x: float | None = None

    # recent dips (x-space) for path-1 clustering
    _dips: deque[float] = field(default_factory=lambda: deque(maxlen=10))
    _cycles_since_learn: int = 0
    _cycles_since_ref_hit: int = 0

    # motion window for path-2 (list of (now_s, x))
    _motion: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=4000))
    _t_last_count_or_learn: float | None = None
    _t_last_learn: float | None = None
    # Observed open-closed span (x-space); drives stroke-proportional re-arm.
    _span_ema: float | None = None
    _recent_cycles: deque[float] = field(default_factory=lambda: deque(maxlen=12))

    def __post_init__(self) -> None:
        if self.cfg.closed_ref is not None:
            self._xref = self._to_x(float(self.cfg.closed_ref))

    # ---- polarity transform (symmetric) ----
    def _to_x(self, pos: float) -> float:
        p = max(0.0, min(1.0, float(pos)))
        return p if self.cfg.closed_polarity != "high" else (1.0 - p)

    def _from_x(self, x: float) -> float:
        return x if self.cfg.closed_polarity != "high" else (1.0 - x)

    @property
    def closed_ref(self) -> float | None:
        return None if self._xref is None else self._from_x(self._xref)

    # ---- smoothing ----
    def _smooth(self, pos: float) -> float:
        win = max(1, int(self.cfg.smooth_win or 1))
        self._win.append(float(pos))
        while len(self._win) > win:
            self._win.popleft()
        s = sorted(self._win)
        return s[len(s) // 2]

    def reset(self) -> None:
        """Signal lost long enough: drop in-progress cycle timing but keep the
        learned closed_ref (like the peak/trough machine)."""
        self._win.clear()
        self._armed = False
        self._in_closed = False
        self._t_last_fire = None
        self._cyc_min_x = None
        self._cyc_max_x = None
        self._warm_min_x = None
        self._warm_max_x = None
        self._span_ema = None
        self._recent_cycles.clear()

    def _effective_span(self) -> float:
        """Best estimate of open-closed travel in x-space."""
        floor = max(self.cfg.closed_hyst * 4.0, self.cfg.stroke_min * 0.5)
        if self._span_ema is not None and self._span_ema > 0:
            return max(self._span_ema, floor)
        if (
            self._warm_min_x is not None
            and self._warm_max_x is not None
            and self._warm_max_x > self._warm_min_x
        ):
            return max(self._warm_max_x - self._warm_min_x, floor)
        if (
            self._cyc_min_x is not None
            and self._cyc_max_x is not None
            and self._cyc_max_x > self._cyc_min_x
        ):
            return max(self._cyc_max_x - self._cyc_min_x, floor)
        return max(self.cfg.stroke_min, floor)

    def _thresholds(self) -> tuple[float, float]:
        """Return (enter_x, rearm_x) in x-space (small == closed)."""
        assert self._xref is not None
        enter_x = self._xref + self.cfg.closed_hyst
        legacy_rearm = self._xref + 2.0 * self.cfg.closed_hyst
        if not self.cfg.stroke_thresholds:
            return enter_x, legacy_rearm
        span = self._effective_span()
        stroke_rearm = self._xref + self.cfg.rearm_stroke_frac * span
        return enter_x, max(legacy_rearm, stroke_rearm)

    def _effective_min_cycle_s(self) -> float:
        floor_cfg = float(self.cfg.min_cycle_s or 0.0)
        if not self.cfg.adaptive_min_cycle or len(self._recent_cycles) < 3:
            return floor_cfg
        s = sorted(self._recent_cycles)
        med = s[len(s) // 2]
        adaptive = med * self.cfg.adaptive_min_cycle_ratio
        if floor_cfg > 0:
            return max(floor_cfg, adaptive)
        return adaptive

    def _note_span_sample(self, span: float) -> None:
        if span < self.cfg.stroke_min * 0.4:
            return
        if self._span_ema is None:
            self._span_ema = span
        else:
            self._span_ema = self._span_ema * 0.75 + span * 0.25

    # ---- main entry ----
    def step(self, pos: float | None, now_s: float | None = None) -> float | None:
        if now_s is None:
            now_s = time.monotonic()
        if pos is None:
            # Hold state; orchestrator decides when to call reset() on long loss.
            return None

        x = self._to_x(self._smooth(pos))
        self._motion.append((now_s, x))
        self._trim_motion(now_s)

        if self._xref is None:
            self._warmup(x, now_s)
            return None

        # First frame with a known reference anchors the idle timer so path-2
        # relearn works even when closed_ref was supplied (no warmup).
        if self._t_last_count_or_learn is None:
            self._t_last_count_or_learn = now_s

        enter_x, rearm_x = self._thresholds()

        # track per-cycle extremes
        self._cyc_min_x = x if self._cyc_min_x is None else min(self._cyc_min_x, x)
        self._cyc_max_x = x if self._cyc_max_x is None else max(self._cyc_max_x, x)

        cycle_s: float | None = None
        armed = self._armed

        # x <= enter_x is closed; x >= rearm_x is open (full retract). The band
        # between them holds the previous zone (park jitter). One count per fresh
        # entry into closed while armed.
        if x >= rearm_x:
            self._in_closed = False
            armed = True
        elif x <= enter_x:
            if not self._in_closed:
                self._in_closed = True
                if armed:
                    accepted, cycle_s = self._fire(now_s)
                    if accepted:
                        armed = False
        self._armed = armed

        # learning paths
        self._maybe_learn(now_s)

        return cycle_s

    # ---- counting helpers ----
    def _fire(self, now_s: float) -> tuple[bool, float | None]:
        cycle_s: float | None = None
        if self._t_last_fire is not None:
            cycle_s = max(0.0, now_s - self._t_last_fire)
            min_s = self._effective_min_cycle_s()
            if min_s > 0 and cycle_s < min_s:
                return False, None
        self.count += 1
        self._t_last_fire = now_s
        self._t_last_count_or_learn = now_s
        if cycle_s is not None and cycle_s > 0:
            self._recent_cycles.append(cycle_s)

        # Stroke span from the half-cycle just completed (closed approach).
        if self._cyc_min_x is not None and self._cyc_max_x is not None:
            self._note_span_sample(self._cyc_max_x - self._cyc_min_x)
        elif self._cyc_max_x is not None and self._xref is not None:
            self._note_span_sample(self._cyc_max_x - self._xref)

        # finalize the dip of the cycle that just closed
        dip = self._cyc_min_x if self._cyc_min_x is not None else None
        if dip is not None:
            self._dips.append(dip)
            if self._xref is not None and dip <= self._xref + self.cfg.drift_tol:
                self._cycles_since_ref_hit = 0
            else:
                self._cycles_since_ref_hit += 1
        self._cyc_min_x = None
        self._cyc_max_x = None
        self._cycles_since_learn += 1
        return True, cycle_s

    def _warmup(self, x: float, now_s: float) -> None:
        self._warm_min_x = x if self._warm_min_x is None else min(self._warm_min_x, x)
        self._warm_max_x = x if self._warm_max_x is None else max(self._warm_max_x, x)
        if (
            self._warm_min_x is not None
            and self._warm_max_x is not None
            and (self._warm_max_x - self._warm_min_x) >= self.cfg.stroke_min
        ):
            # Deepest observed position becomes the closed reference.
            self._xref = self._warm_min_x
            span = self._warm_max_x - self._warm_min_x
            self._note_span_sample(span)
            # Arm only if currently away from the closed band.
            _enter, rearm_x = self._thresholds()
            self._in_closed = x <= _enter
            self._armed = x >= rearm_x
            self._t_last_count_or_learn = now_s

    # ---- learning ----
    def _trim_motion(self, now_s: float) -> None:
        horizon = max(self.cfg.no_count_learn_s * 3.0, 30.0)
        while self._motion and (now_s - self._motion[0][0]) > horizon:
            self._motion.popleft()

    def _cooldown_ok(self, now_s: float) -> bool:
        return self._t_last_learn is None or (now_s - self._t_last_learn) >= self.cfg.cooldown_s

    def _emit_learn(self, kind: str, new_ref_x: float | None, now_s: float, detail: str) -> None:
        ev = LearnEvent(
            kind=kind,
            old_ref=self.closed_ref,
            new_ref=None if new_ref_x is None else self._from_x(new_ref_x),
            now_s=now_s,
            detail=detail,
        )
        self.last_learn = ev
        self.learn_events.append(ev)

    def _maybe_learn(self, now_s: float) -> None:
        if not self.cfg.learn_enabled or self._xref is None:
            return

        # Drift warning (path-1 precursor): dips keep missing the ref.
        if self._cycles_since_ref_hit == self.cfg.warn_every:
            self._emit_learn("drift_warn", None, now_s, f"dip missed ref x{self.cfg.warn_every}")

        # Path 1: counting works, dips clustered but shifted -> nudge ref.
        if (
            self._cycles_since_learn >= self.cfg.learn_every
            and len(self._dips) >= self.cfg.learn_every
        ):
            self._cycles_since_learn = 0
            dips = sorted(self._dips)
            spread = dips[-1] - dips[0]
            med = dips[len(dips) // 2]
            if spread <= self.cfg.cluster_tol and abs(med - self._xref) > self.cfg.drift_tol:
                if self._cooldown_ok(now_s) and self._stroke_ok():
                    new_x = self._limited_jump(self._xref, med)
                    old = self._xref
                    self._xref = new_x
                    self._t_last_learn = now_s
                    self._cycles_since_ref_hit = 0
                    self._emit_learn(
                        "update_path1", new_x, now_s,
                        f"med={med:.3f} from={old:.3f}",
                    )

        # Path 2: no counts for a while but real motion -> relearn ref.
        if self._t_last_count_or_learn is not None:
            idle = now_s - self._t_last_count_or_learn
            if idle >= self.cfg.no_count_learn_s and self._cooldown_ok(now_s):
                span, wmin_x = self._window_span(now_s)
                if span >= self.cfg.stroke_min:
                    old = self._xref
                    new_x = self._limited_jump(self._xref, wmin_x)
                    self._xref = new_x
                    # thresholds reset; drop cycle timing so no bogus duration
                    self._armed = False
                    self._in_closed = False
                    self._t_last_fire = None
                    self._t_last_learn = now_s
                    self._t_last_count_or_learn = now_s
                    self._cycles_since_ref_hit = 0
                    self._emit_learn(
                        "relearn_path2", new_x, now_s,
                        f"span={span:.3f} newmin={self._from_x(wmin_x):.3f} from={self._from_x(old):.3f}",
                    )

    def _limited_jump(self, cur_x: float, target_x: float) -> float:
        d = target_x - cur_x
        if d > self.cfg.max_jump:
            d = self.cfg.max_jump
        elif d < -self.cfg.max_jump:
            d = -self.cfg.max_jump
        return max(0.0, min(1.0, cur_x + d))

    def _stroke_ok(self) -> bool:
        span, _ = self._window_span(self._motion[-1][0]) if self._motion else (0.0, 0.0)
        return span >= self.cfg.stroke_min

    def _window_span(self, now_s: float) -> tuple[float, float]:
        """Return (x_span, x_min) over the recent motion window."""
        lo = None
        hi = None
        for t, x in self._motion:
            if now_s - t > max(self.cfg.no_count_learn_s, 10.0):
                continue
            lo = x if lo is None else min(lo, x)
            hi = x if hi is None else max(hi, x)
        if lo is None or hi is None:
            return 0.0, self._xref if self._xref is not None else 0.0
        return hi - lo, lo
