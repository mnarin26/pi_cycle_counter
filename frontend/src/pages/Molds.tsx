import { useCallback, useEffect, useState } from "react";
import { datetimeLocalInputToUtcIso } from "../lib/chartTheme";
import { apiDelete, apiDownloadCsv, apiGet, apiPatch, apiPost } from "../api/client";

const DEFAULT_STDEV_RATIO = 0.05;
const MIN_STDEV_LIMIT = 0.25;

type Mold = {
  id: number;
  name: string | null;
  status: string;
  avg_cycle_s: number;
  tolerance_s: number;
  stdev_limit_s: number | null;
  sample_count: number;
  confidence: number;
};

function effectiveStdevLimit(m: Pick<Mold, "avg_cycle_s" | "stdev_limit_s">): number {
  if (m.stdev_limit_s != null && m.stdev_limit_s > 0) return m.stdev_limit_s;
  if (m.avg_cycle_s > 0) return Math.max(MIN_STDEV_LIMIT, m.avg_cycle_s * DEFAULT_STDEV_RATIO);
  return MIN_STDEV_LIMIT;
}

function autoStdevHint(avgStr: string): string {
  const avg = parseFloat(avgStr);
  if (!Number.isFinite(avg) || avg <= 0) return "Otomatik: ort. sürenin %5'i (min 0,25 sn)";
  return `Otomatik: ${Math.max(MIN_STDEV_LIMIT, avg * DEFAULT_STDEV_RATIO).toFixed(2)} sn`;
}

type MoldUsageRow = {
  mold_id: number;
  mold_name: string;
  status: string;
  total_cycles: number;
  avg_cycle_s: number;
  machines: Array<{
    machine_id: number;
    machine_name: string;
    cycle_count: number;
  }>;
};

type MoldUsageResponse = {
  range: string;
  from: string;
  to: string;
  rows: MoldUsageRow[];
};

type EditDraft = {
  name: string;
  status: "candidate" | "active" | "ignored";
  avg_cycle_s: string;
  tolerance_s: string;
  stdev_auto: boolean;
  stdev_limit_s: string;
};

function draftFromMold(m: Mold): EditDraft {
  return {
    name: m.name ?? "",
    status: (m.status as EditDraft["status"]) || "candidate",
    avg_cycle_s: String(m.avg_cycle_s),
    tolerance_s: String(m.tolerance_s),
    stdev_auto: m.stdev_limit_s == null,
    stdev_limit_s: m.stdev_limit_s != null ? String(m.stdev_limit_s) : "",
  };
}

function MoldCard({
  mold,
  onChanged,
}: {
  mold: Mold;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() => draftFromMold(mold));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) setDraft(draftFromMold(mold));
  }, [mold, editing]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const avg = parseFloat(draft.avg_cycle_s);
      const tol = parseFloat(draft.tolerance_s);
      if (!Number.isFinite(avg) || avg <= 0) throw new Error("Ort. döngü geçerli bir sayı olmalı");
      if (!Number.isFinite(tol) || tol <= 0) throw new Error("Eşleşme toleransı geçerli bir sayı olmalı");
      let stdev_limit_s: number | null = null;
      if (!draft.stdev_auto) {
        const st = parseFloat(draft.stdev_limit_s);
        if (!Number.isFinite(st) || st <= 0) throw new Error("Stabilite eşiği geçerli bir sayı olmalı");
        stdev_limit_s = st;
      }
      await apiPatch<Mold>(`/api/molds/${mold.id}`, {
        name: draft.name.trim() || null,
        status: draft.status,
        avg_cycle_s: avg,
        tolerance_s: tol,
        stdev_limit_s,
      });
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const label = mold.name || `Kalıp #${mold.id}`;
    if (!confirm(`"${label}" silinsin mi?\n\nGeçmiş döngülerde kalıp bağlantısı kaldırılır.`)) return;
    setBusy(true);
    setErr(null);
    try {
      await apiDelete(`/api/molds/${mold.id}`);
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-slate-700 bg-panel2 p-4">
      {!editing ? (
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-[200px] flex-1">
            <div className="font-medium">{mold.name || "İsimsiz kalıp önerisi"}</div>
            <div className="text-xs text-slate-400">
              Durum: {mold.status} · Ort. {mold.avg_cycle_s.toFixed(2)}s · Eşleşme ±{mold.tolerance_s.toFixed(2)}s
              · Stab. eşik {effectiveStdevLimit(mold).toFixed(2)}s
              {mold.stdev_limit_s == null ? " (oto)" : ""} · n=
              {mold.sample_count} · güven {(mold.confidence * 100).toFixed(0)}%
            </div>
          </div>
          <button
            type="button"
            className="min-h-[44px] rounded bg-slate-600 px-3 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            Düzenle
          </button>
          <button
            type="button"
            className="min-h-[44px] rounded bg-red-900/80 px-3 text-sm text-red-100 disabled:opacity-50"
            disabled={busy}
            onClick={() => void remove()}
          >
            Sil
          </button>
          {mold.status === "candidate" && (
            <>
              <button
                type="button"
                className="min-h-[44px] rounded bg-slate-800 px-3 text-sm disabled:opacity-50"
                disabled={busy}
                onClick={async () => {
                  const name = prompt("Kalıp adı?");
                  if (name) {
                    await apiPost(`/api/molds/${mold.id}/name`, { name });
                    onChanged();
                  }
                }}
              >
                Ad ver
              </button>
              <button
                type="button"
                className="min-h-[44px] rounded bg-slate-800 px-3 text-sm disabled:opacity-50"
                disabled={busy}
                onClick={async () => {
                  await apiPost(`/api/molds/${mold.id}/ignore`);
                  onChanged();
                }}
              >
                Yok say
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-sm sm:col-span-2">
              <span className="mb-1 block text-xs text-slate-400">Ad</span>
              <input
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Durum</span>
              <select
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.status}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, status: e.target.value as EditDraft["status"] }))
                }
              >
                <option value="active">active</option>
                <option value="candidate">candidate</option>
                <option value="ignored">ignored</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Ort. döngü (s)</span>
              <input
                type="number"
                step="0.01"
                min="0.1"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.avg_cycle_s}
                onChange={(e) => setDraft((d) => ({ ...d, avg_cycle_s: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Eşleşme toleransı (± s)</span>
              <input
                type="number"
                step="0.01"
                min="0.01"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.tolerance_s}
                onChange={(e) => setDraft((d) => ({ ...d, tolerance_s: e.target.value }))}
              />
            </label>
            <label className="text-sm sm:col-span-2">
              <span className="mb-1 block text-xs text-slate-400">Stabilite eşiği (s)</span>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={draft.stdev_auto}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        stdev_auto: e.target.checked,
                        stdev_limit_s: e.target.checked ? "" : d.stdev_limit_s,
                      }))
                    }
                  />
                  Otomatik
                </label>
                {!draft.stdev_auto && (
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    className="min-w-[120px] flex-1 rounded border border-slate-600 bg-slate-900 px-2 py-2"
                    value={draft.stdev_limit_s}
                    onChange={(e) => setDraft((d) => ({ ...d, stdev_limit_s: e.target.value }))}
                    placeholder="örn. 0.70"
                  />
                )}
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Duruş sonrası pencere std. sapma limiti. {autoStdevHint(draft.avg_cycle_s)}
              </p>
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded bg-accent px-3 py-2 text-sm text-panel disabled:opacity-50"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? "Kaydediliyor…" : "Kaydet"}
            </button>
            <button
              type="button"
              className="rounded bg-slate-700 px-3 py-2 text-sm disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setErr(null);
              }}
            >
              İptal
            </button>
          </div>
        </div>
      )}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}

export function MoldsPage() {
  const [rows, setRows] = useState<Mold[]>([]);
  const [usage, setUsage] = useState<MoldUsageResponse | null>(null);
  const [range, setRange] = useState<"daily" | "weekly" | "monthly" | "yearly">("weekly");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [loadingMolds, setLoadingMolds] = useState(true);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [exportBusy, setExportBusy] = useState<{ moldId: number; kind: "summary" | "cycles" } | null>(
    null,
  );
  const [err, setErr] = useState<string | null>(null);

  const usageQuery = useCallback(() => {
    const p = new URLSearchParams({ range });
    if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
    if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
    return p.toString();
  }, [range, fromInput, toInput]);

  const loadMolds = useCallback(async () => {
    setLoadingMolds(true);
    try {
      const molds = await apiGet<Mold[]>("/api/molds");
      setRows(molds);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoadingMolds(false);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    setLoadingUsage(true);
    try {
      const usageResp = await apiGet<MoldUsageResponse>(`/api/molds/usage?${usageQuery()}`);
      setUsage(usageResp);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoadingUsage(false);
    }
  }, [usageQuery]);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadMolds(), loadUsage()]);
  }, [loadMolds, loadUsage]);

  const downloadMoldExport = useCallback(
    async (moldId: number, kind: "summary" | "cycles") => {
      setExportBusy({ moldId, kind });
      try {
        const p = new URLSearchParams({ range, kind, mold_id: String(moldId) });
        if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
        if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
        await apiDownloadCsv(`/api/molds/export?${p.toString()}`, `kalip_${moldId}_${kind}.csv`);
      } catch (e) {
        setErr(String(e));
      } finally {
        setExportBusy(null);
      }
    },
    [range, fromInput, toInput],
  );

  useEffect(() => {
    void loadMolds();
  }, [loadMolds]);

  useEffect(() => {
    void loadUsage();
  }, [loadUsage]);

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Kalıplar</h2>
      {err && <p className="mb-3 text-sm text-red-300">{err}</p>}

      <div className="mb-4 rounded border border-slate-700 bg-panel2 p-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex gap-2">
            {(["daily", "weekly", "monthly", "yearly"] as const).map((r) => (
              <button
                key={r}
                type="button"
                className={`rounded px-3 py-2 text-sm ${range === r ? "bg-accent text-panel" : "bg-slate-700"}`}
                onClick={() => setRange(r)}
              >
                {r === "daily" ? "Günlük" : r === "weekly" ? "Haftalık" : r === "monthly" ? "Aylık" : "Yıllık"}
              </button>
            ))}
          </div>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Başlangıç</span>
            <input
              type="datetime-local"
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={fromInput}
              onChange={(e) => setFromInput(e.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Bitiş</span>
            <input
              type="datetime-local"
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={toInput}
              onChange={(e) => setToInput(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded bg-accent px-3 py-2 text-sm text-panel"
            onClick={() => void loadUsage()}
          >
            Aralığı Uygula
          </button>
        </div>
      </div>

      {loadingMolds && rows.length === 0 ? (
        <p className="text-sm text-slate-400">Kalıp listesi yükleniyor…</p>
      ) : (
        <div className="space-y-3">
          {rows.map((m) => (
            <MoldCard key={m.id} mold={m} onChanged={() => void reloadAll()} />
          ))}
          {rows.length === 0 && !loadingMolds && (
            <p className="text-sm text-slate-400">Kayıtlı kalıp yok.</p>
          )}
        </div>
      )}

      <div className="relative mt-6 rounded border border-slate-700 bg-panel2 p-4">
        {loadingUsage && (
          <div className="absolute right-3 top-3 text-xs text-sky-300">Üretim detayı yükleniyor…</div>
        )}
        <h3 className="mb-3 text-lg font-semibold">Kalıp Bazlı Makine Üretim Detayı</h3>
        <div className="space-y-3">
          {(usage?.rows ?? []).map((r) => (
            <div key={r.mold_id} className="rounded border border-slate-700 bg-slate-900/40 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-3">
                <div className="font-medium">{r.mold_name}</div>
                <div className="text-xs text-slate-400">Durum: {r.status}</div>
                <div className="text-xs text-slate-400">Toplam Adet: {r.total_cycles}</div>
                <div className="text-xs text-slate-400">Ort. Döngü: {r.avg_cycle_s.toFixed(2)}s</div>
                <div className="ml-auto flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded bg-emerald-800 px-2 py-1 text-xs disabled:opacity-50"
                    disabled={
                      exportBusy?.moldId === r.mold_id && exportBusy.kind === "summary"
                    }
                    onClick={() => void downloadMoldExport(r.mold_id, "summary")}
                  >
                    {exportBusy?.moldId === r.mold_id && exportBusy.kind === "summary"
                      ? "İndiriliyor…"
                      : "Özet CSV"}
                  </button>
                  <button
                    type="button"
                    className="rounded bg-emerald-900 px-2 py-1 text-xs disabled:opacity-50"
                    disabled={
                      exportBusy?.moldId === r.mold_id && exportBusy.kind === "cycles"
                    }
                    onClick={() => void downloadMoldExport(r.mold_id, "cycles")}
                  >
                    {exportBusy?.moldId === r.mold_id && exportBusy.kind === "cycles"
                      ? "İndiriliyor…"
                      : "Döngü CSV"}
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-slate-400">
                    <tr>
                      <th className="py-1 pr-3">Makine</th>
                      <th className="py-1 pr-3">Adet</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.machines.map((mRow) => (
                      <tr key={`${r.mold_id}-${mRow.machine_id}`} className="border-t border-slate-800">
                        <td className="py-1 pr-3">{mRow.machine_name}</td>
                        <td className="py-1 pr-3">{mRow.cycle_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
          {usage && usage.rows.length === 0 && !loadingUsage && (
            <p className="text-sm text-slate-400">Seçilen aralıkta kalıp üretim kaydı yok.</p>
          )}
        </div>
      </div>
    </div>
  );
}
