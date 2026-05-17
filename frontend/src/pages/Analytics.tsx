import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet } from "../api/client";

export function AnalyticsPage() {
  const [range, setRange] = useState<"daily" | "weekly" | "monthly" | "yearly">("daily");
  const [series, setSeries] = useState<{ t: string; cycle_time_s: number }[]>([]);
  const [hist, setHist] = useState<{ bin_edges: number[]; counts: number[] } | null>(null);
  const [summary, setSummary] = useState<{ cycle_count: number; avg_cycle_s: number } | null>(null);

  useEffect(() => {
    (async () => {
      const s = await apiGet<{ cycle_count: number; avg_cycle_s: number }>(`/api/analytics/summary?range=${range}`);
      setSummary(s);
      const ser = await apiGet<{ t: string; cycle_time_s: number }[]>(`/api/analytics/cycles_series?limit=400`);
      setSeries(ser.map((r) => ({ t: r.t.slice(11, 19), cycle_time_s: r.cycle_time_s })));
      setHist(await apiGet(`/api/analytics/histogram?bins=16`));
    })();
  }, [range]);

  const histData =
    hist?.counts.map((c, i) => ({
      name: hist.bin_edges[i]?.toFixed(1) ?? String(i),
      count: c,
    })) ?? [];

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold">Analitik</h2>
      <div className="flex flex-wrap gap-2">
        {(["daily", "weekly", "monthly", "yearly"] as const).map((r) => (
          <button
            key={r}
            type="button"
            className={`min-h-[44px] px-4 rounded ${range === r ? "bg-accent text-panel" : "bg-panel2"}`}
            onClick={() => setRange(r)}
          >
            {r}
          </button>
        ))}
      </div>
      {summary && (
        <div className="text-sm text-slate-300">
          Döngü sayısı: {summary.cycle_count} · Ortalama: {summary.avg_cycle_s}s
        </div>
      )}
      <div className="h-64 rounded border border-slate-700 bg-panel2 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="t" tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <Tooltip contentStyle={{ background: "#1e293b" }} />
            <Line type="monotone" dataKey="cycle_time_s" stroke="#38bdf8" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="h-56 rounded border border-slate-700 bg-panel2 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={histData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 9 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <Tooltip contentStyle={{ background: "#1e293b" }} />
            <Bar dataKey="count" fill="#4ade80" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <button
        type="button"
        className="min-h-[44px] rounded bg-slate-700 px-4"
        onClick={() => {
          const blob = new Blob([JSON.stringify({ series, hist }, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "analytics-export.json";
          a.click();
        }}
      >
        Dışa aktar (JSON)
      </button>
    </div>
  );
}
