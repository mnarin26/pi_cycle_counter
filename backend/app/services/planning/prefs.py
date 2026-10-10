"""User planning preferences (wizard before "Plan üret" / Geçmiş deneme).

`None` prefs means the legacy engine path — behavior identical to the
pre-preferences planner. Any `PlanPrefs` instance turns on the A2 engine
(stock allocation, split, changeover, calendar, risk), each part toggled by
its own field.

Rule levels: "hard" removes time from the calendar; "soft" is a score
penalty that may be relaxed to meet a due date (the plan then says so).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PRIORITIES = ("due", "min_changeover", "shortest", "low_risk")
SPLIT_MODES = ("no", "yes", "if_late")
RISK_MODES = ("avoid", "ignore")
CYCLE_SOURCES = ("history", "target")
WEEKEND_MODES = ("work", "avoid", "off")
RULE_LEVELS = ("off", "soft", "hard")

PRIORITY_LABELS = {
    "due": "Teslim tarihine yetişmek",
    "min_changeover": "En az kalıp değişimi",
    "shortest": "En kısa toplam süre",
    "low_risk": "En düşük risk",
}
SPLIT_LABELS = {"no": "Bölme yok", "yes": "Bölünebilir", "if_late": "Sadece termine yetişmiyorsa böl"}
WEEKEND_LABELS = {"work": "Çalışılır", "avoid": "Mümkünse çalışma", "off": "Çalışılmaz"}
LEVEL_LABELS = {"off": "Farketmez", "soft": "Mümkünse", "hard": "Kesin kural"}


@dataclass
class LineRule:
    """Per order-line calendar override (key = plan line key, e.g. "o:12" / "x:3").

    `hours_per_day` None → the line works all shift hours from production
    settings; `saturday`/`sunday` None → the plan-wide weekend default.
    """

    hours_per_day: float | None = None
    saturday: str | None = None
    sunday: str | None = None

    def is_empty(self) -> bool:
        return self.hours_per_day is None and self.saturday is None and self.sunday is None

    def cache_key(self) -> tuple:
        return (self.hours_per_day, self.saturday, self.sunday)


@dataclass
class PlanPrefs:
    priority: str = "due"
    split: str = "if_late"
    group_same_product: bool = True
    risky_machines: str = "avoid"
    exclude_machine_ids: list[int] = field(default_factory=list)
    exclude_mold_ids: list[int] = field(default_factory=list)
    start_at: str | None = None
    stock_allocation: bool = True
    warn_stock_zero: bool = True
    cycle_source: str = "history"
    changeover: bool = True
    # calendar — working hours come from production shift settings; weekend
    # values are defaults that a line rule may override
    saturday: str = "work"
    sunday: str = "work"
    manual_not_night: str = "off"
    changeover_daytime: str = "off"
    changeover_window: str = "08:00-17:00"
    closed_days: list[str] = field(default_factory=list)
    line_rules: dict[str, LineRule] = field(default_factory=dict)

    def rule_for(self, line_key: str | None) -> LineRule | None:
        return self.line_rules.get(line_key) if line_key else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def start_datetime(self) -> datetime | None:
        if not self.start_at:
            return None
        dt = datetime.fromisoformat(str(self.start_at).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def window_minutes(self) -> tuple[int, int]:
        a, b = (self.changeover_window or "08:00-17:00").split("-", 1)
        return _hhmm(a), _hhmm(b)


def _hhmm(s: str) -> int:
    h, m = (s.strip().split(":") + ["0"])[:2]
    v = int(h) * 60 + int(m)
    if not 0 <= v <= 24 * 60:
        raise ValueError(f"Saat geçersiz: {s}")
    return v


def _choice(src: dict, key: str, allowed: tuple[str, ...], default: str) -> str:
    v = src.get(key, default)
    v = str(v if v is not None else default).strip()
    if v not in allowed:
        raise ValueError(f"{key}: geçersiz seçim «{v}» (izinli: {', '.join(allowed)})")
    return v


def _int_list(src: dict, key: str) -> list[int]:
    raw = src.get(key) or []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{key} liste olmalı")
    return sorted({int(x) for x in raw})


def _line_rules(src: dict) -> dict[str, LineRule]:
    raw = src.get("line_rules") or {}
    if not isinstance(raw, dict):
        raise ValueError("line_rules nesne olmalı")
    out: dict[str, LineRule] = {}
    for key, r in raw.items():
        if not isinstance(r, dict):
            raise ValueError(f"{key}: sipariş kuralı nesne olmalı")
        h = r.get("hours_per_day")
        hours = None
        if h not in (None, ""):
            hours = float(h)
            if not 0.5 <= hours <= 24:
                raise ValueError(f"{key}: günlük çalışma saati 0,5–24 arasında olmalı")
        rule = LineRule(
            hours_per_day=hours,
            saturday=_choice(r, "saturday", WEEKEND_MODES, "work") if r.get("saturday") else None,
            sunday=_choice(r, "sunday", WEEKEND_MODES, "work") if r.get("sunday") else None,
        )
        if not rule.is_empty():
            out[str(key)] = rule
    return out


def prefs_from_dict(raw: dict[str, Any] | None) -> PlanPrefs | None:
    """Validate user input. `None` → None (legacy engine).

    Unknown keys are ignored, so prefs saved by older versions (e.g. the removed
    global `work_pattern`) still load.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Tercihler nesne olmalı")
    d = PlanPrefs()
    p = PlanPrefs(
        priority=_choice(raw, "priority", PRIORITIES, d.priority),
        split=_choice(raw, "split", SPLIT_MODES, d.split),
        group_same_product=bool(raw.get("group_same_product", d.group_same_product)),
        risky_machines=_choice(raw, "risky_machines", RISK_MODES, d.risky_machines),
        exclude_machine_ids=_int_list(raw, "exclude_machine_ids"),
        exclude_mold_ids=_int_list(raw, "exclude_mold_ids"),
        start_at=(str(raw["start_at"]) if raw.get("start_at") else None),
        stock_allocation=bool(raw.get("stock_allocation", d.stock_allocation)),
        warn_stock_zero=bool(raw.get("warn_stock_zero", d.warn_stock_zero)),
        cycle_source=_choice(raw, "cycle_source", CYCLE_SOURCES, d.cycle_source),
        changeover=bool(raw.get("changeover", d.changeover)),
        saturday=_choice(raw, "saturday", WEEKEND_MODES, d.saturday),
        sunday=_choice(raw, "sunday", WEEKEND_MODES, d.sunday),
        manual_not_night=_choice(raw, "manual_not_night", RULE_LEVELS, d.manual_not_night),
        changeover_daytime=_choice(raw, "changeover_daytime", RULE_LEVELS, d.changeover_daytime),
        changeover_window=str(raw.get("changeover_window") or d.changeover_window),
        closed_days=sorted({str(x)[:10] for x in (raw.get("closed_days") or [])}),
        line_rules=_line_rules(raw),
    )
    a, b = p.window_minutes()
    if b <= a:
        raise ValueError("Kalıp değişim saat aralığı geçersiz (başlangıç < bitiş olmalı)")
    for day in p.closed_days:
        datetime.strptime(day, "%Y-%m-%d")
    if p.start_at:
        p.start_datetime()
    return p


def describe_prefs(p: PlanPrefs | None) -> list[str]:
    """Short Turkish summary lines for the plan header."""
    if p is None:
        return ["Tercih seçilmedi — standart plan (önceki davranış)"]
    out = [
        f"Öncelik: {PRIORITY_LABELS[p.priority]}",
        f"Sipariş bölme: {SPLIT_LABELS[p.split]}",
        "Aynı ürün birlikte üretilir" if p.group_same_product else "Aynı ürün siparişleri ayrı planlanır",
        "Sık arıza yapan makinelerden kaçınılır" if p.risky_machines == "avoid" else "Makine arıza geçmişi dikkate alınmaz",
        "Çalışma saatleri: Ayarlar'daki vardiyalar",
        f"Cumartesi: {WEEKEND_LABELS[p.saturday]} · Pazar: {WEEKEND_LABELS[p.sunday]}",
    ]
    if p.line_rules:
        n_hours = sum(1 for r in p.line_rules.values() if r.hours_per_day is not None)
        n_week = sum(1 for r in p.line_rules.values() if r.saturday or r.sunday)
        bits = []
        if n_hours:
            bits.append(f"{n_hours} siparişte günlük saat sınırı")
        if n_week:
            bits.append(f"{n_week} siparişte özel hafta sonu")
        out.append("Siparişe özel: " + ", ".join(bits))
    if p.manual_not_night != "off":
        out.append(f"Manuel kalıp gece vardiyasına konmaz ({LEVEL_LABELS[p.manual_not_night]})")
    if p.changeover_daytime != "off":
        out.append(f"Kalıp değişimi {p.changeover_window} arasında ({LEVEL_LABELS[p.changeover_daytime]})")
    if p.closed_days:
        out.append("Kapalı günler: " + ", ".join(p.closed_days))
    if p.exclude_machine_ids:
        out.append(f"Hariç makine: {len(p.exclude_machine_ids)} adet")
    if p.exclude_mold_ids:
        out.append(f"Hariç kalıp: {len(p.exclude_mold_ids)} adet")
    if not p.stock_allocation:
        out.append("Stok siparişler arasında paylaştırılmaz")
    out.append("Çevrim süresi: " + ("geçmiş gerçek çevrim" if p.cycle_source == "history" else "kalıp hedef çevrimi"))
    return out
