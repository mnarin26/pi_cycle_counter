"""A2 planner (used whenever preferences are given).

Order of decisions (planlama-is-kurallari §2, §5, §6):
  1. stock goes to the earliest due lines first; warn when it hits zero
  2. same-product lines are produced together unless that makes others late
  3. each job picks the best mold×machine by history, priority, due-date fit,
     changeover time, calendar rules and downtime risk
  4. a job may be split across several molds/machines (prefs.split)
Everything stays a suggestion; nothing is assigned automatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any

from app.db.models import Mold, MoldMachineCapability
from app.services.planning import effective_cavity
from app.services.planning.plan_engine import STATS_DAYS, LineIn, PlanContext, _score, _slot
from app.services.planning.prefs import LineRule, PlanPrefs, describe_prefs
from app.services.planning.work_calendar import (
    Interval,
    WorkCalendar,
    consume,
    fit_contiguous,
    worked_between,
)
from app.services.time_windows import DISPLAY_TZ

TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
GROUP_WINDOW_DAYS = {"min_changeover": 14}
DEFAULT_GROUP_WINDOW_DAYS = 7
RISKY_SCORE = 0.5
TIGHT_DUE_DAYS = 5
RISKY_MARGIN_H = 24.0
DEFAULT_CYCLE_S = 30.0

# per priority: late (per h), risk, soft rule, changeover (per h), finish (per h)
WEIGHTS: dict[str, dict[str, float]] = {
    "due": {"late": 0.08, "risk": 0.6, "soft": 0.15, "co": 0.05, "end": 0.004},
    "min_changeover": {"late": 0.03, "risk": 0.4, "soft": 0.15, "co": 0.6, "end": 0.002},
    "shortest": {"late": 0.04, "risk": 0.4, "soft": 0.1, "co": 0.1, "end": 0.03},
    "low_risk": {"late": 0.03, "risk": 2.0, "soft": 0.1, "co": 0.05, "end": 0.003},
}

STATUS_LABELS = {
    "stock": "Stoktan",
    "on_time": "Zamanında",
    "risky": "Riskli",
    "late": "Gecikir",
    "unplanned": "Planlanamadı",
}


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    loc = dt.astimezone(DISPLAY_TZ)
    return f"{loc.day} {TR_MONTHS[loc.month - 1]} {loc:%H:%M}"


def fmt_day(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    loc = dt.astimezone(DISPLAY_TZ)
    return f"{loc.day} {TR_MONTHS[loc.month - 1]}"


def deadline_of(due: datetime | None) -> datetime | None:
    """Excel due dates are calendar days → deliver by the end of that day (Istanbul)."""
    if due is None:
        return None
    d = (due if due.tzinfo else due.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    return datetime.combine(d, time(23, 59, 59), tzinfo=DISPLAY_TZ).astimezone(timezone.utc)


def fmt_qty(n: int | float) -> str:
    return f"{int(n):,}".replace(",", ".")


@dataclass
class LineState:
    line: LineIn
    deadline: datetime | None
    stock_before: int = 0
    stock_used: int = 0
    need: int = 0
    status: str = ""
    reason: str = ""
    eta: datetime | None = None
    machines: list[str] = field(default_factory=list)


@dataclass
class Job:
    product_id: int
    lines: list[LineState]
    qty: int
    deadline: datetime | None
    rule: LineRule | None = None


@dataclass
class Option:
    mold: Mold
    mid: int
    cap: MoldMachineCapability | None
    st: dict | None
    cycle_s: float
    cavity: int
    qty: int
    co_min: int
    co_start: datetime
    prod_start: datetime
    end: datetime
    intervals: list[Interval]
    relaxed: list[str]
    late_h: float
    risk: float
    base: float
    score: float = 0.0


@dataclass
class State:
    machine_free: dict[int, datetime]
    mold_free: dict[int, datetime]
    mounted: dict[int, int]


class Planner:
    def __init__(self, ctx: PlanContext, prefs: PlanPrefs, start: datetime):
        self.ctx = ctx
        self.prefs = prefs
        self.start = start
        self.cal = WorkCalendar(prefs, ctx.shifts, start)
        self.w = WEIGHTS[prefs.priority]
        excluded = set(prefs.exclude_machine_ids)
        self.machines = [mid for mid in ctx.machines if mid not in excluded]
        self.molds_by_id: dict[int, Mold] = {
            int(m.id): m for ms in ctx.molds_by_product.values() for m in ms
        }

    # ---------- candidate generation ----------

    def molds_for(self, product_id: int) -> list[Mold]:
        ex = set(self.prefs.exclude_mold_ids)
        return [m for m in self.ctx.molds_by_product.get(int(product_id), []) if int(m.id) not in ex]

    def pairs(self, mold: Mold) -> list[tuple[int, MoldMachineCapability | None]]:
        caps = self.ctx.caps_by_mold.get(int(mold.id), [])
        if not caps:
            return [(mid, None) for mid in self.machines]
        allowed = set(self.machines)
        return [(int(c.machine_id), c) for c in caps if int(c.machine_id) in allowed]

    def cycle_for(self, mold: Mold, st: dict | None) -> float:
        """History (mold×machine, before T0) or target; without history → target.

        The mold's running `avg_cycle_s` is never used in a backtest (it includes
        cycles after T0); live it is only a last resort when no target is set.
        """
        hist = float(st["avg_cycle_s"]) if st and st.get("avg_cycle_s") else None
        target = float(mold.target_cycle_s) if mold.target_cycle_s else None
        order = (hist, target) if self.prefs.cycle_source == "history" else (target, hist)
        v = next((x for x in order if x), None)
        if v is None and self.ctx.as_of is None and mold.avg_cycle_s and mold.avg_cycle_s > 0:
            v = float(mold.avg_cycle_s)
        return v or DEFAULT_CYCLE_S

    def changeover_minutes(self, state: State, mold: Mold, mid: int) -> int:
        if not self.prefs.changeover:
            return 0
        prev = state.mounted.get(mid)
        if prev == int(mold.id):
            return 0
        prev_mold = self.molds_by_id.get(prev) if prev else None
        removal = int(prev_mold.removal_minutes or 0) if prev_mold else 0
        return removal + int(mold.mount_minutes or 0)

    def place(
        self,
        state: State,
        mold: Mold,
        mid: int,
        cap: MoldMachineCapability | None,
        qty: int,
        deadline: datetime | None,
        rule: LineRule | None = None,
    ) -> Option | None:
        st = self.ctx.stats.get((mold.id, mid))
        cavity = effective_cavity(mold)
        cycle_s = self.cycle_for(mold, st)
        manual = (mold.work_mode or "auto") == "manual"
        t = max(self.start, state.machine_free.get(mid, self.start), state.mold_free.get(int(mold.id), self.start))
        co_min = self.changeover_minutes(state, mold, mid)
        prod_s = max(1, math.ceil(qty / cavity)) * cycle_s
        variants = (True, False) if self.cal.has_soft_rules(manual, rule) else (False,)
        found: list[tuple[bool, datetime, datetime, datetime, list[Interval]]] = []
        for strict in variants:
            if co_min > 0:
                cs = fit_contiguous(self.cal.changeover(manual=manual, strict=strict, rule=rule), t, co_min * 60)
                if cs is None:
                    continue
                ce = cs + timedelta(minutes=co_min)
            else:
                cs = ce = t
            iv = self.cal.production(manual=manual, strict=strict, rule=rule)
            r = consume(iv, ce, prod_s)
            if r is None:
                continue
            ps, end = r
            found.append((strict, cs if co_min > 0 else ps, ps, end, iv))
        if not found:
            return None
        strict_hit = next((f for f in found if f[0]), None)
        if strict_hit and (deadline is None or strict_hit[3] <= deadline):
            pick = strict_hit
        else:
            pick = min(found, key=lambda f: (f[3], not f[0]))
        is_strict, cs, ps, end, iv = pick
        relaxed: list[str] = []
        if not is_strict and len(variants) == 2:
            relaxed = self.cal.relaxed_labels(cs, end, manual=manual, rule=rule)
            if co_min > 0 and self.prefs.changeover_daytime == "soft":
                strict_co = self.cal.changeover(manual=manual, strict=True, rule=rule)
                if fit_contiguous(strict_co, cs, co_min * 60) != cs:
                    relaxed.append("kalıp değişimi gündüz saatleri dışına kondu")
        late_h = max(0.0, (end - deadline).total_seconds() / 3600.0) if deadline else 0.0
        risk = float(self.ctx.risk.get(mid, {}).get("score", 0.0))
        urgency = (deadline - self.start).total_seconds() / 3600.0 if deadline else None
        prefer_speed = urgency is not None and urgency < 72
        base = _score(st, prefer_speed, priority=cap.priority if cap else 50)
        opt = Option(
            mold=mold, mid=mid, cap=cap, st=st, cycle_s=cycle_s, cavity=cavity, qty=qty,
            co_min=co_min, co_start=cs, prod_start=ps, end=end, intervals=iv,
            relaxed=relaxed, late_h=late_h, risk=risk, base=base,
        )
        opt.score = self.score(opt, deadline)
        return opt

    def score(self, o: Option, deadline: datetime | None) -> float:
        w = self.w
        tight = deadline is not None and (deadline - self.start) <= timedelta(days=TIGHT_DUE_DAYS)
        risk_pen = 0.0
        if self.prefs.risky_machines == "avoid" and (tight or self.prefs.priority == "low_risk"):
            risk_pen = o.risk + (1.0 if o.risk >= RISKY_SCORE else 0.0)
        end_h = (o.end - self.start).total_seconds() / 3600.0
        s = o.base - w["late"] * o.late_h - w["risk"] * risk_pen - w["soft"] * len(o.relaxed)
        s -= w["co"] * (o.co_min / 60.0) + w["end"] * end_h
        if self.prefs.priority == "min_changeover" and o.co_min == 0:
            s += 0.2
        return s

    def options(self, state: State, job: Job, qty: int | None = None) -> list[Option]:
        out: list[Option] = []
        for mold in self.molds_for(job.product_id):
            for mid, cap in self.pairs(mold):
                o = self.place(state, mold, mid, cap, qty or job.qty, job.deadline, job.rule)
                if o:
                    out.append(o)
        out.sort(key=lambda o: (-o.score, o.cap.priority if o.cap else 99, o.mold.id, o.mid))
        return out

    # ---------- split ----------

    def split(self, state: State, job: Job, opts: list[Option]) -> list[Option] | None:
        parts: list[Option] = []
        used_molds: set[int] = set()
        used_machines: set[int] = set()
        for o in opts:
            if o.mold.id in used_molds or o.mid in used_machines:
                continue
            parts.append(o)
            used_molds.add(o.mold.id)
            used_machines.add(o.mid)
            if len(parts) == 3:
                break
        if len(parts) < 2:
            return None
        chunk = max(1, math.ceil(job.qty / 24))
        alloc = [0] * len(parts)
        current: list[Option | None] = [None] * len(parts)
        left = job.qty
        while left > 0:
            step = min(chunk, left)
            best_i, best_opt = None, None
            for i, p in enumerate(parts):
                cand = self.place(state, p.mold, p.mid, p.cap, alloc[i] + step, job.deadline, job.rule)
                if cand and (best_opt is None or cand.end < best_opt.end):
                    best_i, best_opt = i, cand
            if best_i is None:
                return None
            alloc[best_i] += step
            current[best_i] = best_opt
            left -= step
        chosen = [c for c in current if c is not None]
        return chosen if len(chosen) >= 2 else None

    # ---------- main ----------

    def allocate_stock(self, lines: list[LineIn], stock: dict[str, int]) -> tuple[list[LineState], list[str]]:
        states = [LineState(line=ln, deadline=deadline_of(ln.due)) for ln in lines]
        warnings: list[str] = []
        stock = self._usable_stock(states, stock, warnings)
        if not self.prefs.stock_allocation:
            for s in states:
                stk = int(stock.get(s.line.skey(), 0))
                s.stock_before = stk
                s.stock_used = min(stk, s.line.qty)
                s.need = max(0, s.line.qty - stk)
            return states, warnings
        by_key: dict[str, list[LineState]] = {}
        for s in states:
            by_key.setdefault(s.line.skey(), []).append(s)
        far = datetime.max.replace(tzinfo=timezone.utc)
        for key, group in by_key.items():
            group.sort(key=lambda s: (s.deadline or far, s.line.key))
            initial = left = max(0, int(stock.get(key, 0)))
            for s in group:
                s.stock_before = left
                s.stock_used = min(left, s.line.qty)
                left -= s.stock_used
                s.need = s.line.qty - s.stock_used
            if self.prefs.warn_stock_zero and initial > 0 and left == 0:
                label = group[0].line.product_label
                if all(s.need == 0 for s in group):
                    warnings.append(
                        f"{label}: stok siparişleri tam karşılıyor, üretim gerekmiyor — stok sıfıra iner "
                        f"({fmt_qty(initial)} → 0)"
                    )
                else:
                    warnings.append(f"{label}: stok siparişlerle sıfıra iniyor ({fmt_qty(initial)} → 0)")
        return states, warnings

    def _usable_stock(self, states: list[LineState], stock: dict[str, int], warnings: list[str]) -> dict[str, int]:
        """Hold back each product's `min_stock`; only the excess serves orders."""
        mins = self.ctx.min_stock
        if not mins:
            return stock
        out = dict(stock)
        labels = {s.line.skey(): s.line.product_label for s in states}
        for key, label in labels.items():
            if not key.startswith("p:"):
                continue
            floor = mins.get(int(key[2:]))
            if not floor:
                continue
            have = int(stock.get(key, 0))
            out[key] = max(0, have - floor)
            if have <= floor:
                warnings.append(
                    f"{label}: stok ({fmt_qty(have)}) asgari stok seviyesinde veya altında "
                    f"(asgari {fmt_qty(floor)}) — siparişler üretimden karşılanır"
                )
            else:
                warnings.append(
                    f"{label}: {fmt_qty(floor)} adet asgari stok olarak ayrıldı, siparişlere {fmt_qty(have - floor)} adet kullanıldı"
                )
        return out

    def make_jobs(self, states: list[LineState], group: bool) -> list[Job]:
        jobs: list[Job] = []
        window = timedelta(days=GROUP_WINDOW_DAYS.get(self.prefs.priority, DEFAULT_GROUP_WINDOW_DAYS))
        open_by_product: dict[tuple, Job] = {}
        far = datetime.max.replace(tzinfo=timezone.utc)
        for s in sorted(states, key=lambda s: (s.deadline or far, s.line.key)):
            if s.need <= 0 or not s.line.product_id:
                continue
            pid = int(s.line.product_id)
            rule = self.prefs.rule_for(s.line.key)
            gkey = (pid, rule.cache_key() if rule else None)
            cur = open_by_product.get(gkey) if group else None
            if cur and (
                cur.deadline is None
                or s.deadline is None
                or s.deadline - cur.deadline <= window
            ):
                cur.lines.append(s)
                cur.qty += s.need
                continue
            job = Job(product_id=pid, lines=[s], qty=s.need, deadline=s.deadline, rule=rule)
            jobs.append(job)
            open_by_product[gkey] = job
        if self.prefs.priority == "shortest":
            jobs.sort(key=lambda j: -self._rough_hours(j))
        return jobs

    def _rough_hours(self, job: Job) -> float:
        best = None
        for m in self.molds_for(job.product_id):
            h = math.ceil(job.qty / effective_cavity(m)) * self.cycle_for(m, None) / 3600.0
            best = h if best is None else min(best, h)
        return best or 0.0

    def run(self, lines: list[LineIn], stock: dict[str, int], *, group: bool) -> dict[str, Any]:
        states, warnings = self.allocate_stock(lines, stock)
        state = State(
            machine_free={mid: self.start for mid in self.machines},
            mold_free={},
            mounted=dict(self.ctx.current_mold),
        )
        jobs = self.make_jobs(states, group)
        slots: list[dict[str, Any]] = []
        job_parts: list[tuple[Job, list[Option]]] = []
        for job in jobs:
            opts = self.options(state, job)
            if not opts:
                why = "Bu ürüne bağlı kalıp yok" if not self.ctx.molds_by_product.get(job.product_id) else (
                    "Bu ürünün tüm kalıpları hariç tutuldu" if not self.molds_for(job.product_id)
                    else "Uygun makine yok (duruş, hariç tutma veya uyumluluk) ya da takvimde yer yok"
                )
                for s in job.lines:
                    s.status, s.reason = "unplanned", why
                continue
            best = opts[0]
            parts = [best]
            mode = self.prefs.split
            if mode != "no" and len(opts) > 1:
                if mode == "yes" or best.late_h > 0:
                    sp = self.split(state, job, opts)
                    if sp:
                        sp_end = max(p.end for p in sp)
                        margin = timedelta(hours=1) if mode == "yes" else timedelta(0)
                        if sp_end < best.end - margin:
                            parts = sp
            for p in parts:
                state.machine_free[p.mid] = p.end
                state.mold_free[int(p.mold.id)] = p.end
                state.mounted[p.mid] = int(p.mold.id)
            job_parts.append((job, parts))
            self._line_etas(job, parts)
            for i, p in enumerate(parts, start=1):
                slots.append(self._slot(job, p, i, len(parts)))

        out_slots: list[dict[str, Any]] = []
        for s in states:
            if s.need <= 0:
                s.status = "stock"
                s.reason = (
                    f"Stoktan karşılanır (stok {fmt_qty(s.stock_before)} → {fmt_qty(s.stock_before - s.stock_used)})"
                )
                out_slots.append(_slot(s.line, reason=s.reason, meta={"stock_used": s.stock_used}))
            elif not s.line.product_id:
                s.status = "unplanned"
                s.reason = "Ürün sistemdeki ürünlerle eşleşmedi — önce ürün/kalıp bağlayın"
                out_slots.append(_slot(s.line, product_id=None, reason=s.reason))
            elif s.status == "unplanned":
                out_slots.append(_slot(s.line, reason=s.reason))
        out_slots.extend(slots)
        self._statuses(states)
        return {
            "slots": out_slots,
            "states": states,
            "job_parts": job_parts,
            "warnings": warnings,
        }

    def _line_etas(self, job: Job, parts: list[Option]) -> None:
        names = [self.ctx.machine_names.get(p.mid, f"#{p.mid}") for p in parts]
        lo = min(p.prod_start for p in parts)
        hi = max(p.end for p in parts)
        totals = [max(1.0, worked_between(p.intervals, p.prod_start, p.end)) for p in parts]

        def produced(t: datetime) -> float:
            return sum(
                p.qty * min(1.0, worked_between(p.intervals, p.prod_start, t) / tot)
                for p, tot in zip(parts, totals)
            )

        cum = 0
        for s in job.lines:
            cum += s.need
            s.machines = names
            if cum >= job.qty:
                s.eta = hi
                continue
            a, b = lo, hi
            for _ in range(32):
                mid = a + (b - a) / 2
                if produced(mid) >= cum:
                    b = mid
                else:
                    a = mid
            s.eta = b

    def _statuses(self, states: list[LineState]) -> None:
        for s in states:
            if s.status in ("stock", "unplanned"):
                continue
            if s.deadline is None:
                s.status, s.reason = "on_time", f"Termin yok · tahmini bitiş {fmt_dt(s.eta)}"
                continue
            if s.deadline <= self.start:
                s.status = "late"
                s.reason = f"Plan başlangıcında termin ({fmt_day(s.deadline)}) zaten geçmişti · tahmini bitiş {fmt_dt(s.eta)}"
                continue
            late_h = (s.eta - s.deadline).total_seconds() / 3600.0 if s.eta else 0.0
            if late_h > 0:
                s.status = "late"
                s.reason = f"Tahmini bitiş {fmt_dt(s.eta)} · termin {fmt_day(s.deadline)} — {_hours_text(late_h)} gecikme"
            elif late_h > -RISKY_MARGIN_H:
                s.status = "risky"
                s.reason = f"Tahmini bitiş {fmt_dt(s.eta)} · termine {_hours_text(-late_h)} kala — pay az"
            else:
                s.status = "on_time"
                s.reason = f"Tahmini bitiş {fmt_dt(s.eta)} · termin {fmt_day(s.deadline)}"

    def _slot(self, job: Job, p: Option, i: int, n: int) -> dict[str, Any]:
        ctx = self.ctx
        mname = ctx.machine_names.get(p.mid, f"#{p.mid}")
        kname = ctx.mold_names.get(int(p.mold.id), f"#{p.mold.id}")
        bits = [f"{mname} · {kname} kalıbı, {fmt_qty(p.qty)} adet"]
        if n > 1:
            bits[0] += f" (bölünmüş iş {i}/{n})"
        if len(job.lines) > 1:
            bits.append(f"Aynı ürünün {len(job.lines)} siparişi birlikte üretiliyor")
        st = p.st
        if st and st.get("avg_cycle_s"):
            h = f"Bu kalıp bu makinede son {STATS_DAYS} günde %{round(float(st.get('run_ratio') or 0) * 100)} düzenli çalıştı, çevrim {float(st['avg_cycle_s']):.0f} sn"
            if st.get("target_cycle_s"):
                h += f" (hedef {float(st['target_cycle_s']):.0f} sn)"
            bits.append(h)
        else:
            if p.mold.target_cycle_s:
                bits.append(f"Bu makinede geçmiş kaydı yok; kalıp hedef çevrimi {p.cycle_s:.0f} sn kullanıldı")
            elif p.cycle_s == DEFAULT_CYCLE_S and (self.ctx.as_of is not None or not p.mold.avg_cycle_s):
                bits.append(f"Bu makinede geçmiş kaydı ve kalıp hedef çevrimi yok; {p.cycle_s:.0f} sn varsayıldı")
            else:
                bits.append(f"Bu makinede geçmiş kaydı ve hedef çevrim yok; kalıbın ortalama çevrimi {p.cycle_s:.0f} sn kullanıldı")
        if p.cap is not None:
            bits.append(f"Makine önceliği {p.cap.priority}")
        if p.co_min > 0:
            bits.append(f"Kalıp değişimi {p.co_min} dk ({fmt_dt(p.co_start)})")
        elif self.prefs.changeover:
            bits.append("Kalıp zaten makinede, değişim yok")
        r = ctx.risk.get(p.mid)
        if r and r.get("score", 0) >= 0.3:
            bits.append(f"Dikkat: makine son 30 günde {r['stops_30d']} kez durdu ({r['hours_30d']} sa)")
        if job.rule and job.rule.hours_per_day is not None:
            bits.append(f"Bu sipariş günde en fazla {job.rule.hours_per_day:g} saat çalışır")
        if p.relaxed:
            bits.append("Termine yetişmek için " + ", ".join(p.relaxed))
        if job.deadline:
            when = "zamanında" if p.late_h <= 0 else f"{_hours_text(p.late_h)} gecikme"
            bits.append(f"Tahmini bitiş {fmt_dt(p.end)}; termin {fmt_day(job.deadline)} — {when}")
        else:
            bits.append(f"Tahmini bitiş {fmt_dt(p.end)}")
        first = job.lines[0].line
        return _slot(
            first,
            mold_id=int(p.mold.id),
            machine_id=p.mid,
            suggested_start=p.co_start,
            suggested_end=p.end,
            qty_planned=p.qty,
            reason=". ".join(bits) + ".",
            score=round(p.score, 4),
            meta={
                "line_keys": [s.line.key for s in job.lines],
                "part": i,
                "parts": n,
                "co_min": p.co_min,
                "co_start": p.co_start.isoformat(),
                "prod_start": p.prod_start.isoformat(),
                "cycle_s": round(p.cycle_s, 2),
                "cavity": p.cavity,
                "risk": round(p.risk, 3),
                "relaxed": p.relaxed,
                "late_h": round(p.late_h, 2),
                "deadline": job.deadline.isoformat() if job.deadline else None,
                "manual": (p.mold.work_mode or "auto") == "manual",
                "hours_per_day": job.rule.hours_per_day if job.rule else None,
            },
        )


def _hours_text(h: float) -> str:
    if h >= 48:
        return f"{h / 24:.1f} gün".replace(".0 gün", " gün")
    return f"{max(1, round(h))} sa"


def _late_count(run: dict[str, Any]) -> int:
    return sum(1 for s in run["states"] if s.status == "late" and s.deadline and s.deadline > run["start"])


def _execute(ctx: PlanContext, lines: list[LineIn], stock: dict[str, int], prefs: PlanPrefs, start: datetime):
    planner = Planner(ctx, prefs, start)
    grouped = planner.run(lines, stock, group=prefs.group_same_product)
    grouped["start"] = start
    notes: list[str] = []
    chosen = grouped
    n_groups = sum(1 for j, _ in grouped["job_parts"] if len(j.lines) > 1)
    if prefs.group_same_product and n_groups:
        alone = Planner(ctx, prefs, start).run(lines, stock, group=False)
        alone["start"] = start
        if _late_count(alone) < _late_count(grouped):
            chosen = alone
            notes.append(
                f"Aynı ürünü birlikte üretmek {_late_count(grouped) - _late_count(alone)} siparişi geciktirdiği için "
                "siparişler ayrı üretildi (kalıp değişimine izin verildi)"
            )
        else:
            saved = len(alone["job_parts"]) - len(grouped["job_parts"])
            notes.append(
                f"Aynı ürünün siparişleri birlikte üretildi: {n_groups} grup"
                + (f", {saved} kalıp değişimi daha az" if saved > 0 else "")
            )
    return chosen, notes


def _what_if(ctx, lines, stock, prefs: PlanPrefs, start, base_late: int) -> list[str]:
    if base_late == 0:
        return []
    out: list[str] = []
    trials: list[tuple[PlanPrefs, str]] = []
    if prefs.split == "no":
        trials.append((replace(prefs, split="if_late"), "Bölme kapalı olduğu için"))
    if "off" in (prefs.saturday, prefs.sunday):
        trials.append((
            replace(prefs, saturday="work" if prefs.saturday == "off" else prefs.saturday,
                    sunday="work" if prefs.sunday == "off" else prefs.sunday),
            "Hafta sonu çalışılmadığı için",
        ))
    if any(r.hours_per_day is not None for r in prefs.line_rules.values()):
        no_cap = {k: replace(r, hours_per_day=None) for k, r in prefs.line_rules.items()}
        trials.append((
            replace(prefs, line_rules={k: r for k, r in no_cap.items() if not r.is_empty()}),
            "Siparişe özel günlük saat sınırları yüzünden",
        ))
    if prefs.manual_not_night == "hard":
        trials.append((replace(prefs, manual_not_night="soft"), "Manuel kalıplar geceye konmadığı için"))
    if prefs.exclude_machine_ids:
        trials.append((replace(prefs, exclude_machine_ids=[]), "Hariç tutulan makineler yüzünden"))
    for alt, label in trials:
        run, _ = _execute(ctx, lines, stock, alt, start)
        alt_late = _late_count(run)
        if alt_late < base_late:
            tail = "zamanında biter" if alt_late == 0 else f"gecikme {alt_late} siparişe iner"
            out.append(f"{label} {base_late} sipariş gecikiyor; bu kural esnetilirse {tail}")
    return out


def compute_plan_a2(
    ctx: PlanContext, lines: list[LineIn], stock: dict[str, int], prefs: PlanPrefs
) -> dict[str, Any]:
    start = prefs.start_datetime() or ctx.now
    if start < ctx.now and ctx.as_of is None:
        start = ctx.now
    run, notes = _execute(ctx, lines, stock, prefs, start)
    states: list[LineState] = run["states"]
    late = _late_count(run)
    notes += _what_if(ctx, lines, stock, prefs, start, late)
    relaxed_used = sorted({r for _, parts in run["job_parts"] for p in parts for r in p.relaxed})
    if relaxed_used:
        notes.append("“Mümkünse” tercihleri termin için esnetildi: " + ", ".join(relaxed_used))
    risky = [ctx.machine_names.get(mid, f"#{mid}") for mid, r in sorted(ctx.risk.items())
             if r.get("score", 0) >= RISKY_SCORE and mid in ctx.machines]
    if risky and prefs.risky_machines == "avoid":
        notes.append("Sık arıza yapan makineler termini yakın işlerde son tercih: " + ", ".join(risky))
    splits = sum(1 for _, parts in run["job_parts"] if len(parts) > 1)
    if splits:
        notes.append(f"{splits} iş birden fazla kalıp/makineye bölündü (termin için)")

    warnings = list(run["warnings"])
    unmatched = sorted({s.line.product_label for s in states if not s.line.product_id and s.need > 0})
    if unmatched:
        warnings.append(f"{len(unmatched)} ürün sistemde eşleşmedi: " + ", ".join(unmatched[:6]) + ("…" if len(unmatched) > 6 else ""))
    overdue = sum(1 for s in states if s.deadline and s.deadline <= start)
    if overdue:
        warnings.append(f"{overdue} siparişin termini plan başlangıcından önce")

    parts_all = [p for _, parts in run["job_parts"] for p in parts]
    counts = {k: sum(1 for s in states if s.status == k) for k in STATUS_LABELS}
    dated = [s for s in states if s.status in ("on_time", "risky", "late") and s.deadline]
    kpis = {
        "lines": len(states),
        **counts,
        "on_time_rate": round(
            sum(1 for s in dated if s.status != "late") / len(dated), 3
        ) if dated else None,
        "makespan_h": round(
            (max(p.end for p in parts_all) - min(p.co_start for p in parts_all)).total_seconds() / 3600.0, 1
        ) if parts_all else 0.0,
        "plan_end": max(p.end for p in parts_all).isoformat() if parts_all else None,
        "changeovers": sum(1 for p in parts_all if p.co_min > 0),
        "changeover_min": sum(p.co_min for p in parts_all),
        "planned_qty": sum(p.qty for p in parts_all),
        "stock_used": sum(s.stock_used for s in states),
        "machines_used": len({p.mid for p in parts_all}),
        "splits": splits,
    }
    lines_out = [
        {
            "key": s.line.key,
            "order_line_id": s.line.order_line_id,
            "product_id": s.line.product_id,
            "product_label": s.line.product_label,
            "customer": s.line.customer,
            "qty": s.line.qty,
            "due": s.line.due.isoformat() if s.line.due else None,
            "deadline": s.deadline.isoformat() if s.deadline else None,
            "stock_before": s.stock_before,
            "stock_used": s.stock_used,
            "need": s.need,
            "eta": s.eta.isoformat() if s.eta else None,
            "status": s.status,
            "status_label": STATUS_LABELS.get(s.status, s.status),
            "reason": s.reason,
            "machines": s.machines,
        }
        for s in states
    ]
    return {
        "slots": run["slots"],
        "lines": lines_out,
        "warnings": warnings,
        "explanations": notes,
        "kpis": kpis,
        "prefs_summary": describe_prefs(prefs),
        "plan_start": start.isoformat(),
    }
