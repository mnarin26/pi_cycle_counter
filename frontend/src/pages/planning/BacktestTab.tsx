import { useCallback, useEffect, useState } from "react";
import { apiDelete, apiGet, apiPostForm } from "../../api/client";
import { Gantt } from "./Gantt";
import { Kpi, Notes } from "./PlanResultView";
import { PrefsPanel } from "./PrefsPanel";
import type { CompareResult, InspectReport, PlanPrefs, PlanResult, PrefsDefaults } from "./types";
import { STATUS_STYLE, fmtDate, fmtQty, fromLocalInput, pct, toLocalInput } from "./types";

type BacktestDetail = {
  id: number;
  created_at: string | null;
  note: string | null;
  as_of: string | null;
  horizon_end: string | null;
  prefs: PlanPrefs | null;
  inputs: { orders_filename?: string; stock_filename?: string; stock_source?: string } | null;
  result: PlanResult | null;
};
type BacktestListRow = { id: number; created_at: string | null; note: string | null; as_of: string | null; horizon_end: string | null };

function FilePick({ label, file, onPick }: { label: string; file: File | null; onPick: (f: File | null) => void }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 rounded bg-slate-700 px-3 py-2 text-sm">
      {label}
      <span className="max-w-[220px] truncate text-xs text-slate-300">{file ? file.name : "seçilmedi"}</span>
      <input
        type="file"
        accept=".xlsx,.xlsm"
        className="hidden"
        onChange={(e) => {
          onPick(e.target.files?.[0] ?? null);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function BacktestTab({ defaults }: { defaults: PrefsDefaults | null }) {
  const [ordersFile, setOrdersFile] = useState<File | null>(null);
  const [stockFile, setStockFile] = useState<File | null>(null);
  const [report, setReport] = useState<InspectReport | null>(null);
  const [t0, setT0] = useState("");
  const [t1, setT1] = useState("");
  const [usePrefs, setUsePrefs] = useState(true);
  const [prefs, setPrefs] = useState<PlanPrefs | null>(defaults?.prefs ?? null);
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [cmp, setCmp] = useState<CompareResult | null>(null);
  const [list, setList] = useState<BacktestListRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!prefs && defaults) setPrefs(defaults.prefs);
  }, [defaults, prefs]);

  const refreshList = useCallback(async () => {
    const r = await apiGet<{ rows: BacktestListRow[] }>("/api/planning/backtest");
    setList(r.rows);
  }, []);

  const load = useCallback(async (id: number) => {
    setErr(null);
    const d = await apiGet<BacktestDetail>(`/api/planning/backtest/${id}`);
    setDetail(d);
    setCmp(null);
    setCmp(await apiGet<CompareResult>(`/api/planning/backtest/${id}/compare`));
  }, []);

  useEffect(() => {
    void refreshList().catch(() => {});
  }, [refreshList]);

  async function inspect() {
    if (!ordersFile) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("orders", ordersFile);
      if (stockFile) fd.append("stock", stockFile);
      const r = await apiPostForm<InspectReport>("/api/planning/backtest/inspect", fd);
      setReport(r);
      setT0(toLocalInput(r.t0));
      setT1(toLocalInput(r.horizon_end));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (!ordersFile) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("orders", ordersFile);
      if (stockFile) fd.append("stock", stockFile);
      const a = fromLocalInput(t0);
      const b = fromLocalInput(t1);
      if (a) fd.append("as_of", a);
      if (b) fd.append("horizon_end", b);
      if (usePrefs && prefs) fd.append("prefs", JSON.stringify({ ...prefs, start_at: null }));
      const d = await apiPostForm<{ id: number }>("/api/planning/backtest", fd);
      await load(d.id);
      await refreshList();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("Bu geçmiş deneme silinsin mi? (Canlı plan etkilenmez)")) return;
    await apiDelete(`/api/planning/backtest/${id}`);
    if (detail?.id === id) {
      setDetail(null);
      setCmp(null);
    }
    await refreshList();
  }

  return (
    <div className="space-y-4">
      <div className="rounded border border-slate-700 bg-slate-900/40 p-3 text-sm text-slate-300">
        <p>
          <b className="text-white">Geçmiş deneme:</b> «Planlayıcı o tarihte çalışsaydı ne önerirdi, gerçekte ne oldu?»
          Canlı plana, stoğa ve siparişlere hiçbir şey yazılmaz.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <FilePick label="Sipariş Excel" file={ordersFile} onPick={(f) => { setOrdersFile(f); setReport(null); }} />
        <FilePick label="Stok Excel (isteğe bağlı)" file={stockFile} onPick={(f) => { setStockFile(f); setReport(null); }} />
        <button
          type="button"
          className="rounded bg-sky-700 px-3 py-2 text-sm text-white disabled:opacity-50"
          disabled={!ordersFile || busy}
          onClick={() => void inspect()}
        >
          {busy && !report ? "…" : "Dosyaları incele"}
        </button>
      </div>
      {err && <p className="text-sm text-red-300">{err}</p>}

      {report && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Kpi label="Sipariş satırı" value={String(report.line_count)} sub={`${report.product_count} ürün`} />
            <Kpi
              label="Eşleşen ürün"
              value={`${report.matched_count} / ${report.product_count}`}
              sub={report.matched_count < report.product_count ? "eksikler planlanamaz" : "tamamı"}
            />
            <Kpi label="Veri tarihi (başlangıç)" value={fmtDate(report.t0)} sub={report.t0_source ?? ""} />
            <Kpi label="Son termin" value={fmtDate(report.horizon_end, false)} sub={`ilk termin ${fmtDate(report.first_due, false)}`} />
          </div>
          <Notes warnings={report.warnings} explanations={[]} />
          <details className="rounded border border-slate-700">
            <summary className="cursor-pointer bg-slate-800 px-3 py-2 text-sm text-slate-200">
              Ürün eşleştirme raporu
            </summary>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="text-slate-400">
                  <tr>
                    <th className="px-3 py-1">Excel'deki ürün</th>
                    <th className="px-3 py-1">Sistemdeki ürün</th>
                    <th className="px-3 py-1">Sipariş</th>
                    <th className="px-3 py-1">Toplam</th>
                    <th className="px-3 py-1">Stok</th>
                  </tr>
                </thead>
                <tbody>
                  {report.products.map((p) => (
                    <tr key={p.stock_key} className="border-t border-slate-800">
                      <td className="px-3 py-1">
                        {p.product_name_raw}
                        {p.alt_names.length > 0 && <span className="ml-1 text-xs text-slate-500">({p.alt_names.join(", ")})</span>}
                      </td>
                      <td className="px-3 py-1">
                        {p.product_id ? (
                          <span className="text-emerald-300">
                            {p.product_name} <span className="text-xs text-slate-500">({p.match === "kod" ? "koddan" : "addan"})</span>
                          </span>
                        ) : (
                          <span className="text-amber-300">eşleşmedi</span>
                        )}
                      </td>
                      <td className="px-3 py-1">{p.line_count}</td>
                      <td className="px-3 py-1">{fmtQty(p.total_qty)}</td>
                      <td className="px-3 py-1">{p.stock === null ? "—" : fmtQty(p.stock)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm text-slate-300">
              Başlangıç (T0)
              <input type="datetime-local" className="ml-2 rounded bg-slate-800 px-2 py-1" value={t0} onChange={(e) => setT0(e.target.value)} />
            </label>
            <label className="text-sm text-slate-300">
              Bitiş
              <input type="datetime-local" className="ml-2 rounded bg-slate-800 px-2 py-1" value={t1} onChange={(e) => setT1(e.target.value)} />
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={usePrefs} onChange={(e) => setUsePrefs(e.target.checked)} />
              Tercihleri uygula (kapalıysa standart plan)
            </label>
          </div>
          {usePrefs && prefs && <PrefsPanel value={prefs} onChange={setPrefs} defaults={defaults} showStart={false} />}
          <button
            type="button"
            className="rounded bg-emerald-700 px-4 py-2 text-sm text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => void run()}
          >
            {busy ? "Hesaplanıyor…" : "Denemeyi çalıştır"}
          </button>
        </div>
      )}

      {detail && <BacktestResult detail={detail} cmp={cmp} />}

      {list.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium text-slate-300">Önceki denemeler</h3>
          <ul className="space-y-1 text-sm">
            {list.map((r) => (
              <li key={r.id} className="flex items-center gap-2 rounded border border-slate-800 px-3 py-1">
                <button type="button" className="text-left text-sky-300 hover:underline" onClick={() => void load(r.id)}>
                  #{r.id} · {r.note}
                </button>
                <span className="text-xs text-slate-500">
                  {fmtDate(r.as_of)} → {fmtDate(r.horizon_end, false)}
                </span>
                <button type="button" className="ml-auto rounded bg-slate-800 px-2 py-0.5 text-xs" onClick={() => void remove(r.id)}>
                  Sil
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function BacktestResult({ detail, cmp }: { detail: BacktestDetail; cmp: CompareResult | null }) {
  const [filter, setFilter] = useState<"all" | "problems">("all");
  const result = detail.result;
  const k = cmp?.kpis;
  const rows = (cmp?.rows ?? []).filter((r) => filter === "all" || r.risk.level !== "ok");
  return (
    <div className="space-y-3 border-t border-slate-700 pt-3">
      <h3 className="font-medium text-slate-100">
        Deneme #{detail.id} · {fmtDate(detail.as_of)} → {fmtDate(detail.horizon_end, false)}
        <span className="ml-2 text-xs text-slate-400">
          {detail.inputs?.orders_filename}
          {detail.inputs?.stock_source === "excel" ? ` · stok: ${detail.inputs?.stock_filename}` : " · stok: kayıtlardan"}
        </span>
      </h3>
      <p className="text-xs text-slate-400">
        Tercihler: {(result?.prefs_summary ?? ["Tercih seçilmedi — standart plan"]).join(" · ")}
      </p>
      {k && (
        <div className="flex flex-wrap gap-2">
          <Kpi label="Zamanında (plan)" value={pct(k.on_time_plan)} sub={`${k.dated_lines} termini olan sipariş`} />
          <Kpi label="Zamanında (gerçek)" value={pct(k.on_time_actual)} />
          <Kpi label="Toplam süre" value={`${k.makespan_plan_h ?? "—"} sa`} sub={`gerçek ${k.makespan_actual_h ?? "—"} sa`} />
          <Kpi label="Kalıp değişimi" value={String(k.changeovers_plan ?? "—")} sub={`gerçek ${k.changeovers_actual ?? "—"}`} />
          <Kpi label="Makine uyumu" value={pct(k.machine_match)} sub="öneri = gerçek" />
          <Kpi label="Üretilen" value={fmtQty(k.actual_qty)} sub={`plan ${fmtQty(k.planned_qty)}`} />
        </div>
      )}
      {result && <Notes warnings={result.warnings} explanations={result.explanations} />}
      {!cmp && <p className="text-sm text-slate-400">Gerçekleşen veriler yükleniyor…</p>}

      {cmp && (
        <>
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium text-slate-200">Sipariş bazında: planlanan – gerçekleşen – risk</h4>
            <div className="ml-auto flex gap-1">
              {(["all", "problems"] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`rounded px-2 py-1 text-xs ${filter === f ? "bg-sky-700 text-white" : "bg-slate-800 text-slate-300"}`}
                  onClick={() => setFilter(f)}
                >
                  {f === "all" ? "Tümü" : "Sadece riskli/geciken"}
                </button>
              ))}
            </div>
          </div>
          <div className="overflow-x-auto rounded border border-slate-700">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-800 text-slate-300">
                <tr>
                  <th className="px-3 py-2">Termin</th>
                  <th className="px-3 py-2">Ürün</th>
                  <th className="px-3 py-2">Planlanan</th>
                  <th className="px-3 py-2">Gerçekleşen</th>
                  <th className="px-3 py-2">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-t border-slate-800 align-top">
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(r.deadline, false)}</td>
                    <td className="px-3 py-2">
                      {r.product_label}
                      <div className="text-xs text-slate-500">{fmtQty(r.qty)} adet</div>
                    </td>
                    <td className="px-3 py-2">
                      {r.summary.planned}
                      {r.plan.reason && <div className="mt-1 max-w-md text-xs text-slate-500">{r.plan.reason}</div>}
                    </td>
                    <td className="px-3 py-2">{r.summary.actual}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[r.risk.level] ?? ""}`}>{r.risk.label}</span>
                      <div className="mt-1 text-xs text-slate-300">{r.summary.risk}</div>
                      {r.reasons.length > 1 && (
                        <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
                          {r.reasons.slice(1).map((x, i) => (
                            <li key={i}>{x}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {cmp.suggestions.length > 0 && (
            <div className="space-y-1 rounded border border-violet-800/60 bg-violet-950/30 p-2">
              <h4 className="text-sm font-medium text-violet-100">Alternatif öneriler (yalnız öneri, uygulanmaz)</h4>
              <ul className="space-y-1 text-sm text-violet-100">
                {cmp.suggestions.map((s, i) => (
                  <li key={i}>
                    <b>{s.product_label}:</b> {s.text}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {cmp.gantt.rows.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-sm font-medium text-slate-200">Zaman çizelgesi (plan ve gerçek)</h4>
              <Gantt start={cmp.gantt.start} end={cmp.gantt.end} rows={cmp.gantt.rows} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
