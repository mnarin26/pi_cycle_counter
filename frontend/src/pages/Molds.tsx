import { useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";

type Mold = {
  id: number;
  name: string | null;
  status: string;
  avg_cycle_s: number;
  tolerance_s: number;
  sample_count: number;
  confidence: number;
};

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

export function MoldsPage() {
  const [rows, setRows] = useState<Mold[]>([]);
  const [usage, setUsage] = useState<MoldUsageResponse | null>(null);
  const [range, setRange] = useState<"daily" | "weekly" | "monthly" | "yearly">("monthly");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");

  async function load() {
    const p = new URLSearchParams({ range });
    if (fromInput) p.set("from", new Date(fromInput).toISOString());
    if (toInput) p.set("to", new Date(toInput).toISOString());
    const [molds, usageResp] = await Promise.all([
      apiGet<Mold[]>("/api/molds"),
      apiGet<MoldUsageResponse>(`/api/molds/usage?${p.toString()}`),
    ]);
    setRows(molds);
    setUsage(usageResp);
  }

  useEffect(() => {
    load();
  }, [range]);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Kalıplar</h2>
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
          <button type="button" className="rounded bg-accent px-3 py-2 text-sm text-panel" onClick={load}>
            Aralığı Uygula
          </button>
        </div>
      </div>
      <div className="space-y-3">
        {rows.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 rounded border border-slate-700 bg-panel2 p-4">
            <div className="flex-1 min-w-[200px]">
              <div className="font-medium">{m.name || "İsimsiz kalıp önerisi"}</div>
              <div className="text-xs text-slate-400">
                Ort. {m.avg_cycle_s.toFixed(2)}s · ±{m.tolerance_s.toFixed(2)}s · n={m.sample_count} · güven{" "}
                {(m.confidence * 100).toFixed(0)}%
              </div>
            </div>
            {m.status === "candidate" && (
              <>
                <button
                  type="button"
                  className="min-h-[44px] rounded bg-slate-600 px-3"
                  onClick={async () => {
                    const name = prompt("Kalıp adı?");
                    if (name) await apiPost(`/api/molds/${m.id}/name`, { name });
                    load();
                  }}
                >
                  Ad ver
                </button>
                <button
                  type="button"
                  className="min-h-[44px] rounded bg-slate-800 px-3"
                  onClick={async () => {
                    await apiPost(`/api/molds/${m.id}/ignore`);
                    load();
                  }}
                >
                  Yok say
                </button>
              </>
            )}
          </div>
        ))}
      </div>
      <div className="mt-6 rounded border border-slate-700 bg-panel2 p-4">
        <h3 className="mb-3 text-lg font-semibold">Kalıp Bazlı Makine Üretim Detayı</h3>
        <div className="space-y-3">
          {(usage?.rows ?? []).map((r) => (
            <div key={r.mold_id} className="rounded border border-slate-700 bg-slate-900/40 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-3">
                <div className="font-medium">{r.mold_name}</div>
                <div className="text-xs text-slate-400">Durum: {r.status}</div>
                <div className="text-xs text-slate-400">Toplam Adet: {r.total_cycles}</div>
                <div className="text-xs text-slate-400">Ort. Döngü: {r.avg_cycle_s.toFixed(2)}s</div>
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
          {usage && usage.rows.length === 0 && <p className="text-sm text-slate-400">Seçilen aralıkta kalıp üretim kaydı yok.</p>}
        </div>
      </div>
    </div>
  );
}
