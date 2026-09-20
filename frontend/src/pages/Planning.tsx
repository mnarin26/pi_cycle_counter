import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPatch, apiPost, apiUpload } from "../api/client";

type OrderRow = {
  id: number;
  product_name_raw: string;
  product_id: number | null;
  product_name: string | null;
  qty: number;
  due_date: string | null;
  customer: string | null;
  status: string;
  stock: number | null;
  net_need: number | null;
};

type StockRow = { product_id: number; product_name: string; qty: number };
type PlanSlot = {
  id: number;
  order_line_id: number | null;
  product_id: number | null;
  mold_id: number | null;
  machine_id: number | null;
  suggested_start: string | null;
  suggested_end: string | null;
  qty_planned: number;
  reason: string | null;
  score: number | null;
};
type PlanDetail = {
  id: number;
  created_at: string | null;
  status?: string;
  stale_reason?: string | null;
  slots: PlanSlot[];
};
type CompareRow = {
  slot_id: number;
  suggested_machine_id: number | null;
  actual_machine_id: number | null;
  same_machine: boolean;
  delay_hours: number | null;
  earlier_finish_hours_if_followed: number | null;
  suggested_machine_was_faulted: boolean;
  note: string | null;
};
type StatRow = {
  mold_id: number;
  mold_name: string | null;
  machine_id: number;
  run_ratio: number;
  cycle_gap: number | null;
  count_diff: number | null;
  priority_suggestion: { suggested_priority: number; reason: string } | null;
};

function parseApiError(e: unknown): string {
  return String(e instanceof Error ? e.message : e);
}

function formatPlanWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("tr-TR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso.replace("T", " ").slice(0, 16);
  }
}

export function PlanningPage() {
  const [tab, setTab] = useState<"orders" | "stock" | "plan" | "stats">("orders");
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [stock, setStock] = useState<StockRow[]>([]);
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [compare, setCompare] = useState<CompareRow[]>([]);
  const [stats, setStats] = useState<StatRow[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshOrders = useCallback(async () => {
    const r = await apiGet<{ rows: OrderRow[] }>("/api/planning/orders");
    setOrders(r.rows);
  }, []);
  const refreshStock = useCallback(async () => {
    const r = await apiGet<{ rows: StockRow[] }>("/api/planning/stock");
    setStock(r.rows);
  }, []);
  const refreshStats = useCallback(async () => {
    const r = await apiGet<{ rows: StatRow[] }>("/api/planning/mold-machine-stats?days=30");
    setStats(r.rows);
  }, []);

  const loadPlan = useCallback(async (detail: PlanDetail) => {
    setPlan(detail);
    try {
      const c = await apiGet<{ rows: CompareRow[] }>(`/api/planning/plans/${detail.id}/compare`);
      setCompare(c.rows);
    } catch {
      setCompare([]);
    }
  }, []);

  const refreshCurrentPlan = useCallback(async () => {
    const r = await apiGet<{ plan: PlanDetail | null }>("/api/planning/plans/current");
    if (r.plan) await loadPlan(r.plan);
    else {
      setPlan(null);
      setCompare([]);
    }
  }, [loadPlan]);

  useEffect(() => {
    void refreshCurrentPlan().catch(() => {});
  }, [refreshCurrentPlan]);

  useEffect(() => {
    setErr(null);
    setMsg(null);
    if (tab === "orders") void refreshOrders().catch((e) => setErr(parseApiError(e)));
    if (tab === "stock") void refreshStock().catch((e) => setErr(parseApiError(e)));
    if (tab === "stats") void refreshStats().catch((e) => setErr(parseApiError(e)));
    if (tab === "plan") void refreshCurrentPlan().catch((e) => setErr(parseApiError(e)));
  }, [tab, refreshOrders, refreshStock, refreshStats, refreshCurrentPlan]);

  async function onOrderFile(file: File) {
    setBusy(true);
    setErr(null);
    try {
      const preview = await apiUpload<{
        rows: Array<{
          product_name_raw: string;
          qty: number;
          due_date: string | null;
          customer: string | null;
          note: string | null;
        }>;
        file_hash?: string;
        errors?: string[];
      }>("/api/planning/orders/excel/preview", file);
      if (preview.errors?.length) setMsg(`Uyarı: ${preview.errors.join("; ")}`);
      if (!preview.rows?.length) throw new Error("Önizlemede satır yok");
      const ok = confirm(
        `${preview.rows.length} sipariş satırı (termin sırası). Kaydedilsin mi?\n\nİlk 5:\n` +
          preview.rows
            .slice(0, 5)
            .map((r) => `${r.due_date?.slice(0, 10) || "—"} · ${r.product_name_raw} · ${r.qty}`)
            .join("\n"),
      );
      if (!ok) return;
      const conf = await apiPost<{
        plan?: PlanDetail | null;
        plan_stale?: boolean;
      }>("/api/planning/orders/excel/confirm", {
        filename: file.name,
        file_hash: preview.file_hash,
        rows: preview.rows.map((r) => ({
          product_name_raw: r.product_name_raw,
          qty: r.qty,
          due_date: r.due_date,
          customer: r.customer,
          note: r.note,
        })),
      });
      if (conf.plan) {
        await loadPlan(conf.plan);
        setMsg("Siparişler kaydedildi · plan otomatik üretildi");
        setTab("plan");
      } else if (conf.plan_stale) {
        setMsg("Siparişler kaydedildi · mevcut plan bayat — yenileyin");
        await refreshCurrentPlan();
      } else {
        setMsg("Siparişler kaydedildi");
      }
      await refreshOrders();
    } catch (e) {
      setErr(parseApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onStockFile(file: File) {
    setBusy(true);
    setErr(null);
    try {
      const preview = await apiUpload<{
        rows: Array<{ product_name_raw: string; qty: number }>;
        file_hash?: string;
        errors?: string[];
      }>("/api/planning/stock/excel/preview", file);
      if (preview.errors?.length) setMsg(`Uyarı: ${preview.errors.join("; ")}`);
      if (!preview.rows?.length) throw new Error("Önizlemede satır yok");
      const ok = confirm(`${preview.rows.length} stok satırı Excel'e göre yazılacak. Onaylıyor musunuz?`);
      if (!ok) return;
      const conf = await apiPost<{
        plan?: PlanDetail | null;
        plan_stale?: boolean;
      }>("/api/planning/stock/excel/confirm", {
        file_hash: preview.file_hash,
        rows: preview.rows,
      });
      if (conf.plan) {
        await loadPlan(conf.plan);
        setMsg("Stok güncellendi · plan otomatik üretildi");
        setTab("plan");
      } else if (conf.plan_stale) {
        setMsg("Stok güncellendi · mevcut plan bayat — yenileyin");
        await refreshCurrentPlan();
      } else {
        setMsg("Stok Excel ile güncellendi");
      }
      await refreshStock();
    } catch (e) {
      setErr(parseApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function shipOrder(id: number) {
    if (!confirm("Sipariş gönderildi işaretlensin ve stoktan düşülsün mü?")) return;
    await apiPatch(`/api/planning/orders/${id}`, { status: "shipped" });
    await refreshOrders();
    await refreshStock();
    await refreshCurrentPlan();
  }

  async function generatePlan() {
    setBusy(true);
    setErr(null);
    try {
      const p = await apiPost<PlanDetail>("/api/planning/plans/generate");
      await loadPlan(p);
      setMsg(`Aktif plan güncellendi · ${formatPlanWhen(p.created_at)}`);
      setTab("plan");
    } catch (e) {
      setErr(parseApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const tabs: Array<[typeof tab, string]> = [
    ["orders", "Siparişler"],
    ["stock", "Stok"],
    ["plan", "Plan önerisi"],
    ["stats", "Kalıp×Makine"],
  ];

  const planStale = plan?.status === "stale";

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold text-white">Planlama</h2>
        <div className="flex flex-wrap gap-2">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`rounded px-3 py-2 text-sm ${tab === id ? "bg-sky-700 text-white" : "bg-slate-800 text-slate-300"}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="ml-auto rounded bg-emerald-700 px-3 py-2 text-sm text-white disabled:opacity-50"
          disabled={busy}
          onClick={() => void generatePlan()}
        >
          {busy ? "…" : planStale ? "Planı yenile" : "Plan önerisi üret"}
        </button>
      </div>
      {planStale && (
        <div className="rounded border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          Sipariş/stok değişti — plan güncel değil
          {plan?.stale_reason ? ` (${plan.stale_reason})` : ""}. «Planı yenile» ile yeniden üretin.
        </div>
      )}
      {msg && <p className="text-sm text-emerald-300">{msg}</p>}
      {err && <p className="text-sm text-red-300">{err}</p>}

      {tab === "orders" && (
        <div className="space-y-3">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded bg-slate-700 px-3 py-2 text-sm">
            Sipariş Excel yükle
            <input
              type="file"
              accept=".xlsx,.xlsm"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onOrderFile(f);
                e.target.value = "";
              }}
            />
          </label>
          <div className="overflow-x-auto rounded border border-slate-700">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-800 text-slate-300">
                <tr>
                  <th className="px-3 py-2">Termin</th>
                  <th className="px-3 py-2">Ürün</th>
                  <th className="px-3 py-2">Miktar</th>
                  <th className="px-3 py-2">Stok</th>
                  <th className="px-3 py-2">Net ihtiyaç</th>
                  <th className="px-3 py-2">Durum</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-t border-slate-800">
                    <td className="px-3 py-2">{o.due_date?.slice(0, 10) || "—"}</td>
                    <td className="px-3 py-2">{o.product_name || o.product_name_raw}</td>
                    <td className="px-3 py-2">{o.qty}</td>
                    <td className="px-3 py-2">{o.stock ?? "—"}</td>
                    <td className="px-3 py-2">{o.net_need ?? "—"}</td>
                    <td className="px-3 py-2">{o.status}</td>
                    <td className="px-3 py-2">
                      {o.status === "open" && (
                        <button
                          type="button"
                          className="rounded bg-slate-700 px-2 py-1 text-xs"
                          onClick={() => void shipOrder(o.id)}
                        >
                          Gönderildi
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "stock" && (
        <div className="space-y-3">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded bg-slate-700 px-3 py-2 text-sm">
            Stok Excel yükle
            <input
              type="file"
              accept=".xlsx,.xlsm"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onStockFile(f);
                e.target.value = "";
              }}
            />
          </label>
          <ul className="space-y-1 text-sm">
            {stock.map((s) => (
              <li key={s.product_id} className="rounded border border-slate-700 px-3 py-2">
                {s.product_name}: <span className="font-semibold text-sky-300">{s.qty}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === "plan" && (
        <div className="space-y-4">
          {!plan ? (
            <p className="text-sm text-slate-400">Henüz plan yok. «Plan önerisi üret»e basın.</p>
          ) : (
            <>
              <h3 className="font-medium text-slate-200">
                {planStale ? "Bayat plan" : "Aktif plan"} · {formatPlanWhen(plan.created_at)}
              </h3>
              <div className="overflow-x-auto rounded border border-slate-700">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-slate-800 text-slate-300">
                    <tr>
                      <th className="px-3 py-2">Makine</th>
                      <th className="px-3 py-2">Kalıp</th>
                      <th className="px-3 py-2">Miktar</th>
                      <th className="px-3 py-2">Başlangıç</th>
                      <th className="px-3 py-2">Bitiş</th>
                      <th className="px-3 py-2">Gerekçe</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.slots.map((s) => (
                      <tr key={s.id} className="border-t border-slate-800">
                        <td className="px-3 py-2">{s.machine_id ?? "—"}</td>
                        <td className="px-3 py-2">{s.mold_id ?? "—"}</td>
                        <td className="px-3 py-2">{s.qty_planned}</td>
                        <td className="px-3 py-2">
                          {s.suggested_start?.replace("T", " ").slice(0, 16) || "—"}
                        </td>
                        <td className="px-3 py-2">
                          {s.suggested_end?.replace("T", " ").slice(0, 16) || "—"}
                        </td>
                        <td className="px-3 py-2 text-xs text-slate-400">{s.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3 className="font-medium text-slate-200">Öneri vs gerçekleşen</h3>
              <div className="overflow-x-auto rounded border border-slate-700">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-slate-800 text-slate-300">
                    <tr>
                      <th className="px-3 py-2">Öneri makine</th>
                      <th className="px-3 py-2">Gerçek</th>
                      <th className="px-3 py-2">Gecikme (sa)</th>
                      <th className="px-3 py-2">Erken biterdi (sa)</th>
                      <th className="px-3 py-2">Not</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compare.map((c) => (
                      <tr key={c.slot_id} className="border-t border-slate-800">
                        <td className="px-3 py-2">{c.suggested_machine_id ?? "—"}</td>
                        <td className="px-3 py-2">{c.actual_machine_id ?? "—"}</td>
                        <td className="px-3 py-2">{c.delay_hours ?? "—"}</td>
                        <td className="px-3 py-2">{c.earlier_finish_hours_if_followed ?? "—"}</td>
                        <td className="px-3 py-2 text-xs text-slate-400">{c.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "stats" && (
        <div className="overflow-x-auto rounded border border-slate-700">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-slate-800 text-slate-300">
              <tr>
                <th className="px-3 py-2">Kalıp</th>
                <th className="px-3 py-2">Makine</th>
                <th className="px-3 py-2">Run oranı</th>
                <th className="px-3 py-2">Çevrim gap</th>
                <th className="px-3 py-2">Sayım farkı</th>
                <th className="px-3 py-2">Öncelik önerisi</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={`${s.mold_id}-${s.machine_id}`} className="border-t border-slate-800">
                  <td className="px-3 py-2">{s.mold_name || s.mold_id}</td>
                  <td className="px-3 py-2">{s.machine_id}</td>
                  <td className="px-3 py-2">{s.run_ratio}</td>
                  <td className="px-3 py-2">{s.cycle_gap ?? "—"}</td>
                  <td className="px-3 py-2">{s.count_diff ?? "—"}</td>
                  <td className="px-3 py-2 text-xs text-amber-200">
                    {s.priority_suggestion
                      ? `→ ${s.priority_suggestion.suggested_priority}: ${s.priority_suggestion.reason}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
