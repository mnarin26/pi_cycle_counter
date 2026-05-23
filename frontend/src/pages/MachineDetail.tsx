import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet } from "../api/client";
import {
  chartScrollWidth,
  eventColor,
  formatAxisTime,
  moldColor,
} from "../lib/chartTheme";
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

type CyclePoint = {
  t: string;
  cycle_time_s: number;
  mold_id?: number | null;
  mold: string | null;
};

type EventRow = {
  id: number;
  type: string;
  created_at: string | null;
};

type DashboardPayload = {
  machine_id: number;
  range: string;
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
  series: CyclePoint[];
  series_total: number;
  series_shown: number;
  series_truncated: boolean;
  events: EventRow[];
};

type RangeKey = "daily" | "weekly" | "monthly" | "yearly";

type ChartCycle = {
  t: string;
  t_ms: number;
  cycle_time_s: number;
  mold: string;
  mold_key: string;
  mold_color: string;
};

type ChartEvent = {
  id: number;
  type: string;
  t_ms: number;
  y: number;
  color: string;
};

function CycleTooltipContent({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartCycle }>;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-slate-600 bg-slate-900 px-3 py-2 text-xs shadow-lg">
      <div className="font-medium text-slate-100">{new Date(d.t).toLocaleString("tr-TR")}</div>
      <div className="mt-1 text-slate-300">Döngü: {d.cycle_time_s.toFixed(2)} s</div>
      <div className="text-slate-300">Kalıp: {d.mold}</div>
    </div>
  );
}

function EventTooltipContent({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartEvent }>;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-slate-600 bg-slate-900 px-3 py-2 text-xs shadow-lg">
      <div className="font-medium text-slate-100">
        {new Date(d.t_ms).toLocaleString("tr-TR")}
      </div>
      <div className="mt-1 text-slate-300">Olay: {d.type}</div>
    </div>
  );
}

function ChartLegend({
  moldKeys,
  eventTypes,
}: {
  moldKeys: string[];
  eventTypes: string[];
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-4 text-xs">
      {moldKeys.length > 0 && (
        <div>
          <div className="mb-1 font-semibold text-slate-400">Kalıplar</div>
          <div className="flex flex-wrap gap-2">
            {moldKeys.map((k, i) => (
              <span key={k} className="inline-flex items-center gap-1">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: moldColor(k, i) }}
                />
                <span className="text-slate-300">{k}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {eventTypes.length > 0 && (
        <div>
          <div className="mb-1 font-semibold text-slate-400">Olaylar</div>
          <div className="flex flex-wrap gap-2">
            {eventTypes.map((t) => (
              <span key={t} className="inline-flex items-center gap-1">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: eventColor(t) }}
                />
                <span className="text-slate-300">{t}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function MachineDetailPage() {
  const params = useParams<{ id: string }>();
  const machineId = Number(params.id || 0);
  const { snapshot } = useLiveSnapshot();
  const [machine, setMachine] = useState<Machine | null>(null);
  const [dash, setDash] = useState<DashboardPayload | null>(null);
  const [range, setRange] = useState<RangeKey>("daily");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const lastLiveEmitRef = useRef<number>(-1);
  const loadGenRef = useRef(0);

  const snapMachine = useMemo(
    () => snapshot.machines.find((m) => m.id === machineId) ?? null,
    [snapshot.machines, machineId],
  );

  const buildWindowQuery = useCallback(() => {
    const p = new URLSearchParams({
      machine_id: String(machineId),
      range,
    });
    if (fromInput) p.set("from", new Date(fromInput).toISOString());
    if (toInput) p.set("to", new Date(toInput).toISOString());
    return p.toString();
  }, [machineId, range, fromInput, toInput]);

  useEffect(() => {
    if (!machineId) return;
    apiGet<Machine>(`/api/machines/${machineId}`)
      .then(setMachine)
      .catch((e) => setErr(String(e)));
  }, [machineId]);

  const loadDash = useCallback(async () => {
    if (!machineId) return;
    const gen = ++loadGenRef.current;
    setLoading(true);
    try {
      const data = await apiGet<DashboardPayload>(
        `/api/analytics/machine_dashboard?${buildWindowQuery()}&series_limit=4000&events_limit=500`,
      );
      if (gen !== loadGenRef.current) return;
      setDash(data);
      setErr(null);
    } catch (e) {
      if (gen === loadGenRef.current) setErr(String(e));
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [machineId, buildWindowQuery]);

  useEffect(() => {
    loadDash();
  }, [loadDash]);

  useEffect(() => {
    const liveEmitCount = snapMachine?.dbg_cycle_emit_count;
    if (typeof liveEmitCount !== "number") return;
    if (lastLiveEmitRef.current < 0) {
      lastLiveEmitRef.current = liveEmitCount;
      return;
    }
    if (liveEmitCount > lastLiveEmitRef.current) {
      lastLiveEmitRef.current = liveEmitCount;
      loadDash();
    }
  }, [snapMachine?.dbg_cycle_emit_count, loadDash]);

  const moldColorMap = useMemo(() => {
    const keys = [...new Set((dash?.series ?? []).map((s) => s.mold || "—"))];
    const map = new Map<string, string>();
    keys.forEach((k, i) => map.set(k, moldColor(k, i)));
    return map;
  }, [dash?.series]);

  const cycleChartData = useMemo((): ChartCycle[] => {
    return (dash?.series ?? []).map((x) => {
      const mold = x.mold || "—";
      return {
        t: x.t,
        t_ms: new Date(x.t).getTime(),
        cycle_time_s: Number(x.cycle_time_s.toFixed(3)),
        mold,
        mold_key: mold,
        mold_color: moldColorMap.get(mold) ?? moldColor(mold, 0),
      };
    });
  }, [dash?.series, moldColorMap]);

  const eventChartData = useMemo((): ChartEvent[] => {
    return (dash?.events ?? [])
      .filter((e) => e.created_at)
      .map((e) => ({
        id: e.id,
        type: e.type,
        t_ms: new Date(e.created_at!).getTime(),
        y: 1,
        color: eventColor(e.type),
      }));
  }, [dash?.events]);

  const moldBarData = useMemo(() => {
    return (dash?.mold_breakdown ?? []).map((m, i) => ({
      ...m,
      fill: moldColor(m.mold_name, i),
    }));
  }, [dash?.mold_breakdown]);

  const chartWidth = chartScrollWidth(cycleChartData.length);
  const moldLegendKeys = [...moldColorMap.keys()];
  const eventLegendTypes = [...new Set(eventChartData.map((e) => e.type))];

  const xDomain = useMemo((): [number, number] | undefined => {
    const all = [
      ...cycleChartData.map((c) => c.t_ms),
      ...eventChartData.map((e) => e.t_ms),
    ];
    if (!all.length) return undefined;
    const pad = Math.max(60_000, (Math.max(...all) - Math.min(...all)) * 0.02);
    return [Math.min(...all) - pad, Math.max(...all) + pad];
  }, [cycleChartData, eventChartData]);

  if (!machineId) {
    return <p className="text-alarm">Geçersiz makine id</p>;
  }

  const summary = dash?.summary;

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
      {loading && <p className="text-sm text-slate-400">Grafikler yükleniyor…</p>}

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
          <div className="text-lg font-semibold">
            {summary ? `${summary.last_cycle_s.toFixed(2)}s` : "—"}
          </div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Min/Max Döngü</div>
          <div className="text-lg font-semibold">
            {summary
              ? `${summary.min_cycle_s.toFixed(2)} / ${summary.max_cycle_s.toFixed(2)}s`
              : "—"}
          </div>
        </div>
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Çalışma Analizi (Döngü Trendi)</h3>
          {dash?.series_truncated && (
            <span className="text-xs text-amber-300">
              Grafikte {dash.series_shown.toLocaleString("tr-TR")} /{" "}
              {dash.series_total.toLocaleString("tr-TR")} döngü — yatay kaydır
            </span>
          )}
        </div>
        <p className="mb-2 text-xs text-slate-500">
          X ekseni: tarih/saat. Nokta rengi = kalıp. Üzerine gelince tam zaman görünür.
        </p>
        <div className="overflow-x-auto rounded border border-slate-800">
          <div style={{ width: chartWidth, minWidth: "100%", height: 280 }}>
            <ScatterChart width={chartWidth} height={280} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis
                type="number"
                dataKey="t_ms"
                domain={xDomain}
                tick={{ fill: "#94a3b8", fontSize: 10 }}
                tickFormatter={(ms) => formatAxisTime(ms, range)}
              />
              <YAxis
                dataKey="cycle_time_s"
                tick={{ fill: "#94a3b8", fontSize: 10 }}
                label={{ value: "s", angle: 0, position: "insideLeft", fill: "#94a3b8" }}
              />
              <Tooltip content={<CycleTooltipContent />} />
              <Scatter data={cycleChartData} isAnimationActive={false}>
                {cycleChartData.map((entry, i) => (
                  <Cell key={`${entry.t_ms}-${i}`} fill={entry.mold_color} />
                ))}
              </Scatter>
            </ScatterChart>
          </div>
        </div>

        <h3 className="mb-1 mt-4 text-xs font-semibold text-slate-400">Olaylar (zaman çizgisi)</h3>
        <div className="overflow-x-auto rounded border border-slate-800">
          <div style={{ width: chartWidth, minWidth: "100%", height: 72 }}>
            <ScatterChart width={chartWidth} height={72} margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
              <XAxis
                type="number"
                dataKey="t_ms"
                domain={xDomain}
                hide
              />
              <YAxis dataKey="y" domain={[0, 2]} hide />
              <Tooltip content={<EventTooltipContent />} />
              <Scatter data={eventChartData} isAnimationActive={false}>
                {eventChartData.map((entry) => (
                  <Cell key={entry.id} fill={entry.color} />
                ))}
              </Scatter>
            </ScatterChart>
          </div>
        </div>

        <ChartLegend moldKeys={moldLegendKeys} eventTypes={eventLegendTypes} />
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Kalıp Bazlı Üretim Dağılımı</h3>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={moldBarData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="mold_name" tick={{ fill: "#94a3b8", fontSize: 10 }} interval={0} angle={-12} textAnchor="end" height={56} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              <Bar dataKey="cycle_count" name="Adet">
                {moldBarData.map((entry, i) => (
                  <Cell key={`${entry.mold_name}-${i}`} fill={entry.fill} />
                ))}
              </Bar>
              <Legend />
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
                  <td className="py-1 pr-3">
                    <span
                      className="mr-2 inline-block h-2 w-2 rounded-full"
                      style={{ background: m.fill }}
                    />
                    {m.mold_name || "—"}
                  </td>
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
              {(dash?.series ?? []).slice(-20).map((x, i) => (
                <tr key={`${x.t}-${i}`} className="border-t border-slate-800">
                  <td className="py-1 pr-3">{new Date(x.t).toLocaleString("tr-TR")}</td>
                  <td className="py-1 pr-3">{x.cycle_time_s.toFixed(2)}</td>
                  <td className="py-1 pr-3">{x.mold || "—"}</td>
                </tr>
              ))}
              {(dash?.series?.length ?? 0) === 0 && (
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
          {(dash?.events ?? []).slice(-10).reverse().map((e) => (
            <li key={e.id} className="border-b border-slate-800 pb-1">
              <span
                className="mr-2 inline-block h-2 w-2 rounded-full"
                style={{ background: eventColor(e.type) }}
              />
              <span className="mr-2 text-slate-400">
                {e.created_at ? new Date(e.created_at).toLocaleString("tr-TR") : "—"}
              </span>
              <span className="font-medium">{e.type}</span>
            </li>
          ))}
          {(dash?.events?.length ?? 0) === 0 && <li className="text-slate-500">Olay yok</li>}
        </ul>
      </div>
    </div>
  );
}
