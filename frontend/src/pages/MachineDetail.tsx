import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

type Machine = {
  id: number;
  name: string;
  camera_id: number;
  enabled: boolean;
  threshold_mode: string;
  threshold_min: number;
  threshold_offset: number;
  line_thickness: number;
  reflector_len_min?: number | null;
  reflector_len_max?: number | null;
  debounce_ms: number;
  stability_confirm_ms: number;
  hysteresis: number;
};

type Summary = {
  cycle_count: number;
  avg_cycle_s: number;
};

type CyclePoint = {
  t: string;
  cycle_time_s: number;
  machine_id: number;
  mold_id?: number | null;
  mold: string | null;
};

type EventRow = {
  id: number;
  type: string;
  machine_id: number | null;
  payload: string | null;
  created_at: string | null;
};

type MachineAnalysis = {
  machine_id: number;
  range: "daily" | "weekly" | "monthly" | "yearly";
  from: string;
  to: string;
  summary: {
    cycle_count: number;
    avg_cycle_s: number;
    min_cycle_s: number;
    max_cycle_s: number;
    last_cycle_s: number;
  };
  mold_breakdown: Array<{
    mold_id: number | null;
    mold_name: string;
    cycle_count: number;
    share_pct: number;
    avg_cycle_s: number;
  }>;
  time_buckets: Array<{
    bucket: string;
    cycle_count: number;
    avg_cycle_s: number;
  }>;
};

type RangeKey = "daily" | "weekly" | "monthly" | "yearly";

export function MachineDetailPage() {
  const params = useParams<{ id: string }>();
  const machineId = Number(params.id || 0);
  const { snapshot } = useLiveSnapshot();
  const [machine, setMachine] = useState<Machine | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [series, setSeries] = useState<CyclePoint[]>([]);
  const [analysis, setAnalysis] = useState<MachineAnalysis | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [range, setRange] = useState<RangeKey>("daily");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const lastLiveEmitRef = useRef<number>(-1);

  const snapMachine = useMemo(
    () => snapshot.machines.find((m) => m.id === machineId) ?? null,
    [snapshot.machines, machineId],
  );

  const buildWindowQuery = useCallback(() => {
    const p = new URLSearchParams({ machine_id: String(machineId), range });
    if (fromInput) p.set("from", new Date(fromInput).toISOString());
    if (toInput) p.set("to", new Date(toInput).toISOString());
    return p.toString();
  }, [machineId, range, fromInput, toInput]);

  const load = useCallback(async () => {
    if (!machineId) return;
    try {
      const windowQuery = buildWindowQuery();
      const [m, sum, sr, ev] = await Promise.all([
        apiGet<Machine>(`/api/machines/${machineId}`),
        apiGet<Summary>(`/api/analytics/summary?${windowQuery}`),
        apiGet<CyclePoint[]>(`/api/analytics/cycles_series?${windowQuery}&limit=300`),
        apiGet<EventRow[]>(`/api/events?machine_id=${machineId}&limit=20`),
      ]);
      const detail = await apiGet<MachineAnalysis>(`/api/analytics/machine_analysis?${windowQuery}&limit=3000`);
      setMachine(m);
      setSummary(sum);
      setSeries(sr);
      setAnalysis(detail);
      setEvents(ev);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [machineId, buildWindowQuery]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const liveEmitCount = snapMachine?.dbg_cycle_emit_count;
    if (typeof liveEmitCount !== "number") return;
    if (lastLiveEmitRef.current < 0) {
      lastLiveEmitRef.current = liveEmitCount;
      return;
    }
    if (liveEmitCount > lastLiveEmitRef.current) {
      lastLiveEmitRef.current = liveEmitCount;
      load();
    }
  }, [snapMachine?.dbg_cycle_emit_count, load]);

  if (!machineId) {
    return <p className="text-alarm">Geçersiz makine id</p>;
  }

  const lastCycle = series.length ? series[series.length - 1].cycle_time_s : null;
  const cycleTrendData = useMemo(
    () =>
      series.slice(-120).map((x, i) => ({
        idx: i + 1,
        cycle_time_s: Number(x.cycle_time_s.toFixed(3)),
      })),
    [series],
  );
  const moldBarData = analysis?.mold_breakdown ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">
            {machine?.name || `Makine #${machineId}`} detay
          </h2>
          <p className="text-sm text-slate-400">Sayım doğrulama ekranı</p>
        </div>
        <Link to="/" className="rounded bg-slate-700 px-3 py-2 text-sm">
          Panoya dön
        </Link>
      </div>

      {err && <p className="text-alarm text-sm">{err}</p>}

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Aralık</span>
            <select
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={range}
              onChange={(e) => setRange(e.target.value as RangeKey)}
            >
              <option value="daily">Günlük</option>
              <option value="weekly">Haftalık</option>
              <option value="monthly">Aylık</option>
              <option value="yearly">Yıllık</option>
            </select>
          </label>
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
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-5">
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Canlı Durum</div>
          <div className="text-lg font-semibold">{snapMachine?.state || "—"}</div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Döngü Adedi</div>
          <div className="text-lg font-semibold">{summary?.cycle_count ?? 0}</div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Ort. Döngü</div>
          <div className="text-lg font-semibold">
            {summary ? `${summary.avg_cycle_s.toFixed(2)}s` : "—"}
          </div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Son Döngü</div>
          <div className="text-lg font-semibold">{lastCycle != null ? `${lastCycle.toFixed(2)}s` : "—"}</div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Min/Max Döngü</div>
          <div className="text-lg font-semibold">
            {analysis ? `${analysis.summary.min_cycle_s.toFixed(2)} / ${analysis.summary.max_cycle_s.toFixed(2)}s` : "—"}
          </div>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Çalışma Analizi (Döngü Trendi)</h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={cycleTrendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="idx" tick={{ fill: "#94a3b8", fontSize: 10 }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              <Line type="monotone" dataKey="cycle_time_s" stroke="#38bdf8" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Kalıp Bazlı Üretim Dağılımı</h3>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={moldBarData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="mold_name" tick={{ fill: "#94a3b8", fontSize: 10 }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              <Bar dataKey="cycle_count" fill="#4ade80" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1 pr-3">Kalıp</th>
                <th className="py-1 pr-3">Adet</th>
                <th className="py-1 pr-3">Pay</th>
                <th className="py-1 pr-3">Ort. Döngü</th>
              </tr>
            </thead>
            <tbody>
              {moldBarData.map((m, i) => (
                <tr key={`${m.mold_id ?? "na"}-${i}`} className="border-t border-slate-800">
                  <td className="py-1 pr-3">{m.mold_name || "—"}</td>
                  <td className="py-1 pr-3">{m.cycle_count}</td>
                  <td className="py-1 pr-3">%{m.share_pct.toFixed(1)}</td>
                  <td className="py-1 pr-3">{m.avg_cycle_s.toFixed(2)}s</td>
                </tr>
              ))}
              {moldBarData.length === 0 && (
                <tr>
                  <td className="py-2 text-slate-500" colSpan={4}>
                    Kalıp verisi yok
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Canlı Tespit Telemetrisi</h3>
        <div className="grid gap-2 text-sm md:grid-cols-3">
          <div>Pozisyon: {snapMachine?.position_01 != null ? snapMachine.position_01.toFixed(3) : "—"}</div>
          <div>Prominence: {snapMachine?.prominence ?? "—"}</div>
          <div>Segment Len: {snapMachine?.segment_len ?? "—"}</div>
          <div>
            Peak/Bg: {snapMachine ? `${snapMachine.peak ?? 0}/${snapMachine.background ?? 0}` : "—"}
          </div>
          <div>
            Len Aralığı:{" "}
            {machine?.reflector_len_min != null && machine?.reflector_len_max != null
              ? `${machine.reflector_len_min}-${machine.reflector_len_max}`
              : "—"}
          </div>
          <div>Kamera FPS: {snapMachine?.fps != null ? snapMachine.fps.toFixed(1) : "—"}</div>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Makine Ayarları</h3>
        <div className="grid gap-2 text-sm md:grid-cols-3">
          <div>Debounce: {machine?.debounce_ms ?? "—"} ms</div>
          <div>Sabit Onay: {machine?.stability_confirm_ms ?? "—"} ms</div>
          <div>Hysteresis: {machine?.hysteresis ?? "—"}</div>
          <div>Threshold: {machine ? `${machine.threshold_mode} / ${machine.threshold_min}` : "—"}</div>
          <div>Offset: {machine?.threshold_offset ?? "—"}</div>
          <div>Line Thickness: {machine?.line_thickness ?? "—"}</div>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Son 20 Döngü</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1 pr-3">Zaman</th>
                <th className="py-1 pr-3">Döngü (s)</th>
                <th className="py-1 pr-3">Kalıp</th>
              </tr>
            </thead>
            <tbody>
              {series.slice(-20).map((x, i) => (
                <tr key={`${x.t}-${i}`} className="border-t border-slate-800">
                  <td className="py-1 pr-3">{new Date(x.t).toLocaleTimeString()}</td>
                  <td className="py-1 pr-3">{x.cycle_time_s.toFixed(2)}</td>
                  <td className="py-1 pr-3">{x.mold || "—"}</td>
                </tr>
              ))}
              {series.length === 0 && (
                <tr>
                  <td className="py-2 text-slate-500" colSpan={3}>
                    Kayıt yok
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Son Olaylar</h3>
        <ul className="space-y-1 text-sm">
          {events.slice(0, 10).map((e) => (
            <li key={e.id} className="border-b border-slate-800 pb-1">
              <span className="mr-2 text-slate-400">
                {e.created_at ? new Date(e.created_at).toLocaleTimeString() : "—"}
              </span>
              <span className="font-medium">{e.type}</span>
            </li>
          ))}
          {events.length === 0 && <li className="text-slate-500">Olay yok</li>}
        </ul>
      </div>

      <div className="rounded border border-amber-700 bg-amber-950/20 p-3 text-sm">
        <div className="font-semibold text-amber-300">Doğru sayım nasıl doğrulanır?</div>
        <ul className="mt-2 list-disc pl-5 text-slate-200">
          <li>Makine başında 20 fiziksel çevrim say ve bu sayfadaki artışla karşılaştır.</li>
          <li>Her çevrimde durum akışı MOVING to CLOSED or OPEN to MOVING şeklinde ilerlemeli.</li>
          <li>Reflektör yokken `prominence` düşük, `state` sabit kalmalı; yalancı döngü artmamalı.</li>
        </ul>
      </div>
    </div>
  );
}

