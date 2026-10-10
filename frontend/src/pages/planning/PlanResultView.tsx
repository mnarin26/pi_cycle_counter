import type { PlanResult } from "./types";
import { STATUS_STYLE, fmtDate, fmtQty, pct } from "./types";

export function Notes({ warnings, explanations }: { warnings: string[]; explanations: string[] }) {
  if (!warnings.length && !explanations.length) return null;
  return (
    <div className="grid gap-2 md:grid-cols-2">
      {warnings.length > 0 && (
        <ul className="space-y-1 rounded border border-amber-700/60 bg-amber-950/30 p-2 text-sm text-amber-100">
          {warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}
      {explanations.length > 0 && (
        <ul className="space-y-1 rounded border border-sky-800/60 bg-sky-950/30 p-2 text-sm text-sky-100">
          {explanations.map((w, i) => (
            <li key={i}>• {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-[120px] rounded border border-slate-700 bg-slate-900/60 px-3 py-2">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="text-lg font-semibold text-white">{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export function PlanResultView({ result }: { result: PlanResult }) {
  const k = result.kpis;
  return (
    <div className="space-y-3">
      {result.prefs_summary?.length > 0 && (
        <p className="text-xs text-slate-400">Tercihler: {result.prefs_summary.join(" · ")}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Kpi
          label="Zamanında"
          value={k.on_time_rate == null ? "—" : pct(Number(k.on_time_rate))}
          sub={`${k.late ?? 0} gecikir · ${k.risky ?? 0} riskli${k.unplanned ? ` · ${k.unplanned} planlanamadı` : ""}`}
        />
        <Kpi label="Stoktan" value={String(k.stock ?? 0)} sub="sipariş" />
        <Kpi label="Toplam süre" value={`${k.makespan_h ?? 0} sa`} sub={`bitiş ${fmtDate(String(k.plan_end ?? ""))}`} />
        <Kpi label="Kalıp değişimi" value={String(k.changeovers ?? 0)} sub={`${k.changeover_min ?? 0} dk`} />
        <Kpi label="Üretilecek" value={fmtQty(Number(k.planned_qty ?? 0))} sub={`${k.machines_used ?? 0} makine`} />
        {Number(k.refill_qty ?? 0) > 0 && (
          <Kpi
            label="Asgari stok tamamla"
            value={fmtQty(Number(k.refill_qty))}
            sub={`${k.refill_now ?? 0} hemen · ${k.refill_after ?? 0} plan sonuna`}
          />
        )}
      </div>
      <Notes warnings={result.warnings} explanations={result.explanations} />
      <div className="overflow-x-auto rounded border border-slate-700">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-800 text-slate-300">
            <tr>
              <th className="px-3 py-2">Termin</th>
              <th className="px-3 py-2">Ürün</th>
              <th className="px-3 py-2">Miktar</th>
              <th className="px-3 py-2">Stoktan</th>
              <th className="px-3 py-2">Üretilecek</th>
              <th className="px-3 py-2">Makine</th>
              <th className="px-3 py-2">Durum</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((l) => (
              <tr key={l.key} className="border-t border-slate-800 align-top">
                <td className="px-3 py-2 whitespace-nowrap">{fmtDate(l.due, false)}</td>
                <td className="px-3 py-2">{l.product_label}</td>
                <td className="px-3 py-2">{fmtQty(l.qty)}</td>
                <td className="px-3 py-2">{l.stock_used ? fmtQty(l.stock_used) : "—"}</td>
                <td className="px-3 py-2">{l.need ? fmtQty(l.need) : "—"}</td>
                <td className="px-3 py-2">{l.machines.join(", ") || "—"}</td>
                <td className="px-3 py-2">
                  <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[l.status] ?? ""}`}>{l.status_label}</span>
                  <div className="mt-1 text-xs text-slate-400">{l.reason}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
