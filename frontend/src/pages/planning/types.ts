export type PlanPrefs = {
  priority: "due" | "min_changeover" | "shortest" | "low_risk";
  split: "no" | "yes" | "if_late";
  group_same_product: boolean;
  risky_machines: "avoid" | "ignore";
  exclude_machine_ids: number[];
  exclude_mold_ids: number[];
  start_at: string | null;
  stock_allocation: boolean;
  warn_stock_zero: boolean;
  cycle_source: "history" | "target";
  changeover: boolean;
  saturday: "work" | "avoid" | "off";
  sunday: "work" | "avoid" | "off";
  manual_not_night: "off" | "soft" | "hard";
  changeover_daytime: "off" | "soft" | "hard";
  changeover_window: string;
  closed_days: string[];
  line_rules: Record<string, LineRule>;
};

export type WeekendMode = "work" | "avoid" | "off";

export type LineRule = {
  hours_per_day: number | null;
  saturday: WeekendMode | null;
  sunday: WeekendMode | null;
};

/** One order line as shown in the per-line working-hours editor. */
export type RuleLine = { key: string; label: string; due: string | null; qty: number };

export type PrefsDefaults = {
  prefs: PlanPrefs;
  shifts: Array<{ id: string; name: string; start: string; end: string }>;
  machines: Array<{ id: number; name: string }>;
  molds: Array<{ id: number; name: string; product_id: number | null; work_mode: string }>;
};

export type PlanLine = {
  key: string;
  product_id: number | null;
  product_label: string;
  customer: string | null;
  qty: number;
  due: string | null;
  deadline: string | null;
  stock_before: number;
  stock_used: number;
  need: number;
  eta: string | null;
  status: "stock" | "on_time" | "risky" | "late" | "unplanned";
  status_label: string;
  reason: string;
  machines: string[];
};

export type PlanResult = {
  lines: PlanLine[];
  warnings: string[];
  explanations: string[];
  kpis: Record<string, number | string | null>;
  prefs_summary: string[];
  plan_start?: string;
};

export type InspectReport = {
  orders_filename: string;
  stock_filename: string;
  t0: string | null;
  t0_source: string | null;
  horizon_end: string | null;
  first_due: string | null;
  line_count: number;
  product_count: number;
  matched_count: number;
  products: Array<{
    stock_key: string;
    product_name_raw: string;
    alt_names: string[];
    product_id: number | null;
    product_name: string | null;
    match: "ad" | "kod" | "yok";
    line_count: number;
    total_qty: number;
    stock: number | null;
  }>;
  lines: Array<{
    key: string;
    product_name_raw: string;
    product_name: string | null;
    qty: number;
    due_date: string | null;
  }>;
  unused_stock: Array<{ product_name_raw: string; qty: number }>;
  warnings: string[];
};

export type CompareRow = {
  key: string;
  product_id: number | null;
  product_label: string;
  customer: string | null;
  qty: number;
  need: number;
  stock_used: number | null;
  deadline: string | null;
  plan: {
    status: string;
    status_label: string;
    eta: string | null;
    machines: string[];
    molds: string[];
    qty: number;
    cycle_s: number | null;
    reason: string | null;
  };
  actual: {
    status: string | null;
    status_label: string | null;
    finish: string | null;
    start: string | null;
    qty: number | null;
    machines: string[];
    cycle_s: number | null;
    delay_h: number | null;
  };
  risk: { level: "ok" | "risky" | "late" | "unknown"; label: string };
  reasons: string[];
  summary: { planned: string; actual: string; risk: string };
};

export type GanttBar = { start: string; end: string; label: string; prod_start?: string | null; relevant?: boolean };
export type GanttRow = {
  machine_id: number;
  machine_name: string;
  planned: GanttBar[];
  actual: GanttBar[];
  downtimes: GanttBar[];
};

export type CompareResult = {
  plan_run_id: number;
  as_of: string;
  horizon_end: string;
  window_end: string;
  kpis: {
    on_time_plan: number | null;
    on_time_actual: number | null;
    dated_lines: number;
    makespan_plan_h: number | null;
    makespan_actual_h: number | null;
    changeovers_plan: number | null;
    changeovers_actual: number | null;
    machine_match: number | null;
    qty_deviation: number | null;
    planned_qty: number;
    actual_qty: number;
  };
  rows: CompareRow[];
  gantt: { start: string; end: string; rows: GanttRow[] };
  suggestions: Array<{ key: string; product_label: string; text: string; meets_due: boolean }>;
};

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("tr-TR", {
    day: "numeric",
    month: "short",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function fmtQty(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("tr-TR");
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `%${Math.round(v * 100)}`;
}

export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fromLocalInput(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export const STATUS_STYLE: Record<string, string> = {
  stock: "bg-slate-700 text-slate-100",
  on_time: "bg-emerald-800 text-emerald-100",
  ok: "bg-emerald-800 text-emerald-100",
  risky: "bg-amber-700 text-amber-50",
  late: "bg-red-800 text-red-100",
  unplanned: "bg-slate-600 text-slate-200",
  unknown: "bg-slate-600 text-slate-200",
};
