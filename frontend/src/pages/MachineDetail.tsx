import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiDownloadCsv, apiGet, apiPost } from "../api/client";
import {
  buildZigzagSeries,
  buildZigzagLineSegments,
  buildMoldColorMap,
  moldColorFromMap,
  chartScrollWidth,
  computeZigzagYDomain,
  applyZigzagYScale,
  datetimeLocalInputToUtcIso,
  eventColor,
  formatAxisTime,
  formatZigzagAxisTick,
  parseApiTime,
  zigzagXAxisTicks,
  zigzagXDomain,
  ZIGZAG_RESOLUTION_LABELS,
  ZIGZAG_RESOLUTION_MS,
  type ZigzagResolution,
  zigzagChartWidth,
  zigzagTimelineMs,
  findNearestZigzagCycle,
  resolveZigzagHoverMs,
  formatBucketLabel,
  moldStackKey,
  type ZigzagPoint,
} from "../lib/chartTheme";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";
import { useZigzagViewport } from "../hooks/useZigzagViewport";

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

type TrendMoldSlice = { mold_name: string; count: number };

type TrendBucket = {
  bucket: string;
  t_ms: number;
  cycle_count: number;
  avg_cycle_s: number;
  min_cycle_s: number;
  max_cycle_s: number;
  by_mold: TrendMoldSlice[];
};

type DashboardPayload = {
  machine_id: number;
  range: string;
  from: string;
  to: string;
  window_label?: string;
  chart_mode: "cycles" | "buckets";
  gap_threshold_s?: number;
  summary: {
    cycle_count: number;
    avg_cycle_s: number;
    min_cycle_s: number;
    max_cycle_s: number;
    last_cycle_s: number;
  };
  active_mold: {
    mold_id: number;
    mold_name: string;
    cycle_count: number;
    avg_cycle_s: number;
    min_cycle_s: number;
    max_cycle_s: number;
  } | null;
  mold_breakdown: Array<{
    mold_id: number | null;
    mold_name: string;
    cycle_count: number;
    share_pct: number;
    avg_cycle_s: number;
  }>;
  trend_resolution: string;
  trend_resolution_label: string;
  trend_buckets: TrendBucket[];
  trend_mold_names: string[];
  series: CyclePoint[];
  series_total: number;
  series_lazy?: boolean;
  events: EventRow[];
};

type RangeKey = "daily" | "weekly" | "monthly" | "yearly";

type TrendRow = TrendBucket & {
  label: string;
  [key: string]: string | number | TrendMoldSlice[];
};

const ZIGZAG_SNAP_MAX_MS = 120_000; // 2 dk — daha uzaksa "bu saatte döngü yok"

function ZigzagTooltipContent({
  active,
  label,
  coordinate,
  plotPoints,
  xDomain,
  chartWidth,
  range,
}: {
  active?: boolean;
  label?: string | number;
  coordinate?: { x?: number; y?: number };
  plotPoints: ZigzagPoint[];
  xDomain?: [number, number];
  chartWidth: number;
  range: string;
}) {
  if (!active) return null;

  const hoverMs = resolveZigzagHoverMs(label, coordinate, xDomain, chartWidth);
  if (hoverMs == null) return null;

  const hoverLabel = formatAxisTime(hoverMs, range);
  const nearest = findNearestZigzagCycle(plotPoints, hoverMs);
  const firstT = plotPoints[0]?.t_ms;
  const lastT = plotPoints[plotPoints.length - 1]?.t_ms;
  const outsideLoaded =
    firstT != null &&
    lastT != null &&
    (hoverMs < firstT - ZIGZAG_SNAP_MAX_MS || hoverMs > lastT + ZIGZAG_SNAP_MAX_MS);

  if (!nearest || nearest.deltaMs > ZIGZAG_SNAP_MAX_MS || outsideLoaded) {
    return (
      <div className="rounded border border-amber-700 bg-slate-900 px-3 py-2 text-xs text-amber-200">
        <div className="font-medium text-slate-100">{hoverLabel}</div>
        <div className="mt-1">Bu saatte döngü yok (mola / duruş)</div>
      </div>
    );
  }

  const d = nearest.point;
  const cycleLabel = formatAxisTime(d.t_ms, range);
  const snapNote =
    nearest.deltaMs > 5000
      ? `İmleç: ${hoverLabel} · en yakın döngü (+${Math.round(nearest.deltaMs / 1000)} sn)`
      : null;
  const rawS = d.cycle_time_s_raw ?? d.cycle_time_s!;
  const clipped = rawS !== d.cycle_time_s;

  return (
    <div className="rounded border border-slate-600 bg-slate-900 px-3 py-2 text-xs shadow-lg">
      <div className="font-medium text-slate-100">{cycleLabel}</div>
      {snapNote && <div className="mt-0.5 text-slate-500">{snapNote}</div>}
      <div className="mt-1 text-slate-300">Döngü: {rawS.toFixed(2)} s</div>
      {d.is_post_gap && (
        <div className="text-amber-300">Duruş sonrası ilk döngü (mola süresi dahil)</div>
      )}
      {clipped && !d.is_post_gap && (
        <div className="text-slate-500">Grafikte ölçek için üst sınıra kısaltıldı</div>
      )}
      <div className="text-slate-300">Kalıp: {d.mold}</div>
    </div>
  );
}

function BucketTooltipContent({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: TrendRow }>;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-slate-600 bg-slate-900 px-3 py-2 text-xs shadow-lg max-w-xs">
      <div className="font-medium text-slate-100">{d.bucket}</div>
      <div className="mt-1 text-slate-300">
        Ort: {d.avg_cycle_s.toFixed(2)} s · Min–max: {d.min_cycle_s.toFixed(2)}–{d.max_cycle_s.toFixed(2)} s
      </div>
      <div className="text-slate-300">Döngü: {d.cycle_count.toLocaleString("tr-TR")}</div>
      {d.by_mold?.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-t border-slate-700 pt-1">
          {d.by_mold.map((m) => (
            <li key={m.mold_name} className="text-slate-400">
              {m.mold_name}: {m.count.toLocaleString("tr-TR")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChartLegend({
  molds,
  zigzag = false,
}: {
  molds: { name: string; color: string }[];
  zigzag?: boolean;
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-4 text-xs">
      <div>
        <div className="mb-1 font-semibold text-slate-400">Grafik</div>
        <div className="flex flex-wrap gap-3 text-slate-300">
          {zigzag ? (
            <>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 bg-sky-400" />
                Döngü süresi (renk = kalıp)
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 border-t border-dashed border-amber-400" />
                Duruş boşluğu (çizgi kopuk)
              </span>
            </>
          ) : (
            <>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 bg-sky-400" />
                Ort. döngü süresi (çizgi)
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-3 w-3 rounded-sm bg-slate-500/50" />
                Döngü adedi (yığılmış çubuk)
              </span>
            </>
          )}
        </div>
      </div>
      {molds.length > 0 && (
        <div>
          <div className="mb-1 font-semibold text-slate-400">Kalıplar</div>
          <div className="flex flex-wrap gap-2">
            {molds.map((m) => (
              <span key={m.name} className="inline-flex items-center gap-1">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ background: m.color }}
                />
                <span className="text-slate-300">{m.name}</span>
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
  const [replayMode, setReplayMode] = useState<"missing_only" | "reprocess">("missing_only");
  const [replayBusy, setReplayBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState<"summary" | "cycles" | null>(null);
  const [replayMsg, setReplayMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [zigzagResolution, setZigzagResolution] = useState<ZigzagResolution>("6h");
  const lastLiveEmitRef = useRef<number>(-1);
  const liveDashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadGenRef = useRef(0);
  const machineLoadedRef = useRef(0);

  const snapMachine = useMemo(
    () => snapshot.machines.find((m) => m.id === machineId) ?? null,
    [snapshot.machines, machineId],
  );

  const buildWindowQuery = useCallback(() => {
    const p = new URLSearchParams({
      machine_id: String(machineId),
      range,
      lazy_series: "true",
    });
    if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
    if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
    return p.toString();
  }, [machineId, range, fromInput, toInput]);

  useEffect(() => {
    if (!machineId) return;
    if (machineLoadedRef.current !== machineId) {
      machineLoadedRef.current = machineId;
      setMachine(null);
      setDash(null);
    }
  }, [machineId]);

  const loadDash = useCallback(async () => {
    if (!machineId) return;
    const gen = ++loadGenRef.current;
    setLoading(true);
    try {
      const eventsCap =
        range === "yearly" ? 60 : range === "monthly" ? 80 : range === "weekly" ? 120 : 100;
      const dashPath = `/api/analytics/machine_dashboard?${buildWindowQuery()}&events_limit=${eventsCap}`;
      const [machineData, dashData] = await Promise.all([
        apiGet<Machine>(`/api/machines/${machineId}`),
        apiGet<DashboardPayload>(dashPath),
      ]);
      if (gen !== loadGenRef.current) return;
      setMachine(machineData);
      setDash(dashData);
      setErr(null);
    } catch (e) {
      if (gen === loadGenRef.current) setErr(String(e));
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [machineId, buildWindowQuery, range]);

  const runReplay = useCallback(async () => {
    if (!machineId) return;
    setReplayBusy(true);
    setReplayMsg(null);
    try {
      const body: Record<string, unknown> = { range, mode: replayMode };
      if (fromInput) body.from = datetimeLocalInputToUtcIso(fromInput);
      if (toInput) body.to = datetimeLocalInputToUtcIso(toInput);
      const res = await apiPost<{
        cycles_total: number;
        cycles_assigned: number;
        cycles_skipped_existing: number;
        events_created: number;
      }>(`/api/machines/${machineId}/replay-mold-matching`, body);
      setReplayMsg(
        `${res.cycles_total} döngü tarandı · ${res.cycles_assigned} kalıp atandı` +
          (res.cycles_skipped_existing
            ? ` · ${res.cycles_skipped_existing} zaten kayıtlıydı (atlanıldı)`
            : "") +
          (res.events_created ? ` · ${res.events_created} olay` : ""),
      );
      await loadDash();
    } catch (e) {
      setReplayMsg(String(e));
    } finally {
      setReplayBusy(false);
    }
  }, [machineId, range, replayMode, fromInput, toInput, loadDash]);

  const downloadExport = useCallback(
    async (kind: "summary" | "cycles") => {
      if (!machineId) return;
      setExportBusy(kind);
      try {
        const p = new URLSearchParams({ range, kind });
        if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
        if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
        await apiDownloadCsv(
          `/api/machines/${machineId}/export?${p.toString()}`,
          `makine_${machineId}_${kind}.csv`,
        );
      } catch (e) {
        setErr(String(e));
      } finally {
        setExportBusy(null);
      }
    },
    [machineId, range, fromInput, toInput],
  );

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
      if (liveDashTimerRef.current) clearTimeout(liveDashTimerRef.current);
      liveDashTimerRef.current = setTimeout(() => {
        liveDashTimerRef.current = null;
        loadDash();
      }, 5000);
    }
    return () => {
      if (liveDashTimerRef.current) {
        clearTimeout(liveDashTimerRef.current);
        liveDashTimerRef.current = null;
      }
    };
  }, [snapMachine?.dbg_cycle_emit_count, loadDash]);

  const chartMode = dash?.chart_mode ?? "cycles";
  const seriesLazy = chartMode === "cycles" && (dash?.series_lazy ?? true);
  const trendMoldNames = dash?.trend_mold_names ?? [];
  const gapMs = (dash?.gap_threshold_s ?? 1200) * 1000;
  const totalCycles = dash?.series_total ?? dash?.summary?.cycle_count ?? 0;
  const filterFromMs = fromInput ? parseApiTime(datetimeLocalInputToUtcIso(fromInput)) : NaN;
  const filterToMs = toInput ? parseApiTime(datetimeLocalInputToUtcIso(toInput)) : NaN;
  const zigzagVisibleMs = ZIGZAG_RESOLUTION_MS[zigzagResolution];

  const viewportResetKey = `${machineId}|${range}|${fromInput}|${toInput}`;

  const viewport = useZigzagViewport({
    machineId,
    range,
    windowFrom: dash?.from,
    windowTo: dash?.to,
    visibleSpanMs: zigzagVisibleMs,
    enabled: seriesLazy && !!dash,
    resetKey: viewportResetKey,
  });

  const zigzagSource = useMemo(() => {
    const raw = seriesLazy ? viewport.mergedSeries : (dash?.series ?? []);
    const lo = Number.isFinite(filterFromMs) ? filterFromMs : viewport.windowFromMs;
    const hi = Number.isFinite(filterToMs) ? filterToMs : viewport.windowToMs;
    return raw.filter((p) => {
      const t = parseApiTime(p.t);
      return t >= lo && t <= hi;
    });
  }, [
    seriesLazy,
    viewport.mergedSeries,
    viewport.windowFromMs,
    viewport.windowToMs,
    dash?.series,
    filterFromMs,
    filterToMs,
  ]);

  const moldColorMap = useMemo(() => {
    const names: string[] = [];
    for (const m of dash?.mold_breakdown ?? []) names.push(m.mold_name);
    for (const p of zigzagSource) names.push(p.mold || "—");
    for (const n of trendMoldNames) names.push(n);
    return buildMoldColorMap(names);
  }, [dash?.mold_breakdown, zigzagSource, trendMoldNames]);

  const moldLegend = useMemo(
    () => [...moldColorMap.entries()].map(([name, color]) => ({ name, color })),
    [moldColorMap],
  );

  const zigzagDataRaw = useMemo(() => {
    if (chartMode !== "cycles") return [];
    return buildZigzagSeries(zigzagSource, gapMs, moldColorMap);
  }, [chartMode, zigzagSource, gapMs, moldColorMap]);

  const moldStatsForScale = dash?.active_mold ?? dash?.summary;

  const zigzagYDomain = useMemo((): [number, number] => {
    return computeZigzagYDomain(zigzagDataRaw, {
      avgCycleS: moldStatsForScale?.avg_cycle_s,
      maxCycleS: moldStatsForScale?.max_cycle_s,
    });
  }, [zigzagDataRaw, moldStatsForScale?.avg_cycle_s, moldStatsForScale?.max_cycle_s]);

  const zigzagData = useMemo(
    () => applyZigzagYScale(zigzagDataRaw, zigzagYDomain[1]),
    [zigzagDataRaw, zigzagYDomain],
  );

  const zigzagHasClipped = useMemo(
    () =>
      zigzagDataRaw.some((p) => {
        const raw = p.cycle_time_s_raw ?? p.cycle_time_s;
        return raw != null && raw > zigzagYDomain[1] * 1.01;
      }),
    [zigzagDataRaw, zigzagYDomain],
  );

  const zigzagLineSegments = useMemo(
    () => (chartMode === "cycles" ? buildZigzagLineSegments(zigzagData) : []),
    [chartMode, zigzagData],
  );

  const zigzagPlotPoints = useMemo(
    () => zigzagData.filter((p) => p.cycle_time_s != null && !p.is_gap),
    [zigzagData],
  );

  const trendChartData = useMemo((): TrendRow[] => {
    if (chartMode !== "buckets") return [];
    const res = dash?.trend_resolution ?? "hour";
    return (dash?.trend_buckets ?? []).map((b) => {
      const row: TrendRow = {
        ...b,
        label: formatBucketLabel(b.bucket, res),
      };
      for (const m of b.by_mold) {
        row[moldStackKey(m.mold_name)] = m.count;
      }
      return row;
    });
  }, [chartMode, dash?.trend_buckets, dash?.trend_resolution]);


  const moldBarData = useMemo(() => {
    return (dash?.mold_breakdown ?? []).map((m) => ({
      ...m,
      fill: moldColorFromMap(moldColorMap, m.mold_name),
    }));
  }, [dash?.mold_breakdown, moldColorMap]);

  const chartPointCount =
    chartMode === "cycles" ? zigzagData.length : trendChartData.length;
  const chartWidth = useMemo(() => {
    if (chartMode !== "cycles") {
      return chartScrollWidth(chartPointCount, 44);
    }
    if (seriesLazy) return viewport.chartWidth;
    const lo = Number.isFinite(filterFromMs) ? filterFromMs : viewport.windowFromMs;
    const hi = Number.isFinite(filterToMs) ? filterToMs : viewport.windowToMs;
    const timeline = zigzagTimelineMs(range, lo, hi);
    return zigzagChartWidth(timeline, zigzagVisibleMs, 960);
  }, [
    chartMode,
    seriesLazy,
    viewport.chartWidth,
    chartPointCount,
    range,
    zigzagVisibleMs,
    filterFromMs,
    filterToMs,
    viewport.windowFromMs,
    viewport.windowToMs,
  ]);

  const xDomain = useMemo((): [number, number] | undefined => {
    if (chartMode === "cycles" && viewport.windowToMs > viewport.windowFromMs) {
      const lo = Number.isFinite(filterFromMs) ? filterFromMs : viewport.windowFromMs;
      const hi = Number.isFinite(filterToMs) ? filterToMs : viewport.windowToMs;
      return zigzagXDomain(range, lo, hi);
    }
    const all = [
      ...(chartMode === "cycles"
        ? zigzagData.map((c) => c.t_ms)
        : trendChartData.map((c) => c.t_ms)),
    ];
    if (!all.length) return undefined;
    const pad = Math.max(60_000, (Math.max(...all) - Math.min(...all)) * 0.02);
    return [Math.min(...all) - pad, Math.max(...all) + pad];
  }, [chartMode, range, viewport, filterFromMs, filterToMs, zigzagData, trendChartData]);

  const zigzagXTicks = useMemo(
    () => (chartMode === "cycles" ? zigzagXAxisTicks(xDomain, zigzagResolution) : []),
    [chartMode, xDomain, zigzagResolution],
  );

  if (!machineId) {
    return <p className="text-alarm">Geçersiz makine id</p>;
  }

  const summary = dash?.summary;
  const activeMold = dash?.active_mold ?? null;
  // Prefer active-mold stats for avg/min/max; fall back to global summary
  const moldStats = activeMold ?? summary;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">
            {machine?.name || `Makine #${machineId}`} detay
          </h2>
        </div>
        <div className="flex items-center gap-3">
          {loading && dash && <span className="text-xs text-sky-300">Güncelleniyor…</span>}
          <Link to="/" className="rounded bg-slate-700 px-3 py-2 text-sm">
            Panoya dön
          </Link>
        </div>
      </div>

      {err && <p className="text-alarm text-sm">{err}</p>}
      {loading && !dash && <p className="text-sm text-slate-400">Veriler yükleniyor…</p>}

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
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Geçmiş işleme</span>
            <select
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={replayMode}
              onChange={(e) => setReplayMode(e.target.value as "missing_only" | "reprocess")}
              disabled={replayBusy}
            >
              <option value="missing_only">Sadece boş olanlar</option>
              <option value="reprocess">Aralığı yeniden işle</option>
            </select>
          </label>
          <button
            type="button"
            className="rounded bg-amber-700 px-3 py-2 text-sm font-medium disabled:opacity-50"
            disabled={replayBusy || loading}
            onClick={() => void runReplay()}
          >
            {replayBusy ? "İşleniyor…" : "Kalıp eşleştirmesini çalıştır"}
          </button>
          <button
            type="button"
            className="rounded bg-emerald-800 px-3 py-2 text-sm font-medium disabled:opacity-50"
            disabled={!!exportBusy || loading}
            onClick={() => void downloadExport("summary")}
          >
            {exportBusy === "summary" ? "İndiriliyor…" : "Özet CSV indir"}
          </button>
          <button
            type="button"
            className="rounded bg-emerald-900 px-3 py-2 text-sm font-medium disabled:opacity-50"
            disabled={!!exportBusy || loading}
            onClick={() => void downloadExport("cycles")}
          >
            {exportBusy === "cycles" ? "İndiriliyor…" : "Döngü CSV indir"}
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Yukarıdaki aralık kullanılır (günlük ≈ bugün, haftalık ≈ bu hafta). En fazla 7 gün.
          <strong className="font-normal text-slate-400"> Sadece boş olanlar:</strong> daha önce
          kayıtlı kalıplara dokunulmaz.
          <strong className="font-normal text-slate-400"> Yeniden işle:</strong> seçili aralıktaki
          atamalar silinip matcher baştan çalışır.
        </p>
        {replayMsg && <p className="mt-1 text-xs text-amber-200">{replayMsg}</p>}
      </div>

      <div className="grid gap-3 md:grid-cols-6">
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Canlı Durum</div>
          <div className="text-lg font-semibold">{snapMachine?.state || "—"}</div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Toplam Döngü</div>
          <div className="text-lg font-semibold">{summary?.cycle_count ?? 0}</div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3 md:col-span-2">
          <div className="text-xs text-slate-400">
            Aktif Kalıp
            {activeMold && (
              <span className="ml-1 text-slate-500">· {activeMold.cycle_count} döngü</span>
            )}
          </div>
          <div className="truncate text-lg font-semibold text-amber-300">
            {activeMold?.mold_name ?? (dash && !loading ? "—" : snapMachine?.mold_name ?? "—")}
          </div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">
            Ort. Döngü{activeMold ? " (kalıp)" : ""}
          </div>
          <div className="text-lg font-semibold">
            {moldStats ? `${moldStats.avg_cycle_s.toFixed(2)}s` : "—"}
          </div>
        </div>
        <div className="rounded border border-slate-700 bg-panel2 p-3">
          <div className="text-xs text-slate-400">Son Döngü</div>
          <div className="text-lg font-semibold">
            {summary ? `${summary.last_cycle_s.toFixed(2)}s` : "—"}
          </div>
        </div>
      </div>
      {moldStats && summary && moldStats !== summary && (
        <div className="rounded border border-slate-700 bg-panel2 px-3 py-2 text-sm text-slate-400">
          Aktif kalıp min/max: {moldStats.min_cycle_s.toFixed(2)} s – {moldStats.max_cycle_s.toFixed(2)} s
          <span className="ml-3 text-slate-500">
            (tüm kalıplar: {summary.min_cycle_s.toFixed(2)} – {summary.max_cycle_s.toFixed(2)} s)
          </span>
        </div>
      )}

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Çalışma Analizi (Döngü Trendi)</h3>
          <div className="flex flex-wrap items-end gap-3">
            {chartMode === "cycles" && (
              <label className="text-xs text-slate-400">
                <span className="mb-1 block">Ekran çözünürlüğü</span>
                <select
                  className="rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm text-slate-100"
                  value={zigzagResolution}
                  onChange={(e) => setZigzagResolution(e.target.value as ZigzagResolution)}
                >
                  {(Object.keys(ZIGZAG_RESOLUTION_MS) as ZigzagResolution[]).map((k) => (
                    <option key={k} value={k}>
                      {ZIGZAG_RESOLUTION_LABELS[k]}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="text-right text-xs text-slate-400">
              {dash?.window_label && <div>{dash.window_label}</div>}
            </div>
          </div>
        </div>
        {zigzagHasClipped && chartMode === "cycles" && (
          <p className="mb-2 text-xs text-slate-500">
            Uzun duruş sonrası veya aşırı döngüler Y ekseninde kısaltılır; gerçek süre tooltip&apos;te
            gösterilir (üst sınır ≈ {zigzagYDomain[1]} s).
          </p>
        )}
        {totalCycles > 0 && (seriesLazy || dash?.series_truncated) && (
          <p className="mb-2 text-xs text-amber-200">
            {seriesLazy
              ? `${viewport.mergedSeries.length.toLocaleString("tr-TR")} / ${totalCycles.toLocaleString("tr-TR")} döngü`
              : `${dash!.series_shown.toLocaleString("tr-TR")} / ${dash!.series_total.toLocaleString("tr-TR")} döngü`}
          </p>
        )}
        <div
          ref={seriesLazy ? viewport.scrollRef : undefined}
          className="overflow-x-auto rounded border border-slate-800"
        >
          <div style={{ width: chartWidth, minWidth: "100%", height: 300 }}>
            {chartMode === "cycles" ? (
              <ComposedChart
                width={chartWidth}
                height={300}
                data={[]}
                margin={{ top: 8, right: 16, bottom: 24, left: 8 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  type="number"
                  dataKey="t_ms"
                  domain={xDomain}
                  ticks={zigzagXTicks}
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  tickFormatter={(ms) => formatZigzagAxisTick(ms, zigzagResolution)}
                  height={36}
                />
                <YAxis
                  domain={zigzagYDomain}
                  allowDataOverflow
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  label={{ value: "s", angle: 0, position: "insideLeft", fill: "#94a3b8" }}
                />
                <Tooltip
                  content={
                    <ZigzagTooltipContent
                      plotPoints={zigzagPlotPoints}
                      xDomain={xDomain}
                      chartWidth={chartWidth}
                      range={range}
                    />
                  }
                  cursor={{ stroke: "#94a3b8", strokeWidth: 1, strokeDasharray: "4 4" }}
                  isAnimationActive={false}
                  shared={false}
                  filterNull={false}
                />
                {zigzagLineSegments.map((seg, i) => (
                  <Line
                    key={`${seg.mold}-${i}`}
                    type="linear"
                    data={seg.points}
                    dataKey="cycle_time_s"
                    stroke={seg.color}
                    strokeWidth={2}
                    dot={false}
                    activeDot={false}
                    connectNulls={false}
                    isAnimationActive={false}
                    tooltipType="none"
                  />
                ))}
              </ComposedChart>
            ) : (
              <ComposedChart
                width={chartWidth}
                height={300}
                data={trendChartData}
                margin={{ top: 8, right: 48, bottom: 24, left: 8 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  interval="preserveStartEnd"
                  angle={trendChartData.length > 14 ? -35 : 0}
                  textAnchor={trendChartData.length > 14 ? "end" : "middle"}
                  height={trendChartData.length > 14 ? 52 : 28}
                />
                <YAxis
                  yAxisId="left"
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  label={{ value: "s", angle: 0, position: "insideLeft", fill: "#94a3b8" }}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  label={{ value: "adet", angle: 0, position: "insideRight", fill: "#64748b" }}
                />
                <Tooltip content={<BucketTooltipContent />} />
                {trendMoldNames.map((name) => (
                  <Bar
                    key={name}
                    yAxisId="right"
                    dataKey={moldStackKey(name)}
                    stackId="volume"
                    fill={moldColorFromMap(moldColorMap, name)}
                    isAnimationActive={false}
                  />
                ))}
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="avg_cycle_s"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={trendChartData.length <= 31}
                  isAnimationActive={false}
                />
              </ComposedChart>
            )}
          </div>
        </div>


        <ChartLegend
          molds={moldLegend}
          zigzag={chartMode === "cycles"}
        />
      </div>

      <div className="rounded border border-slate-700 bg-panel2 p-3">
        <h3 className="mb-2 text-sm font-semibold">Kalıp Bazlı Üretim Dağılımı</h3>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={moldBarData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis
                dataKey="mold_name"
                tick={{ fill: "#94a3b8", fontSize: 10 }}
                interval={0}
                angle={-12}
                textAnchor="end"
                height={56}
              />
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
                  <td className="py-1 pr-3">
                    {new Date(parseApiTime(x.t)).toLocaleString("tr-TR", {
                      timeZone: "Europe/Istanbul",
                    })}
                  </td>
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
