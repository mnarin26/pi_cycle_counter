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

export function MoldsPage() {
  const [rows, setRows] = useState<Mold[]>([]);

  async function load() {
    setRows(await apiGet<Mold[]>("/api/molds"));
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Kalıplar</h2>
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
    </div>
  );
}
