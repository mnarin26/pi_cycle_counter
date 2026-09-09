import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet, apiPost } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useLiveSnapshot, type MachineSnap } from "../hooks/useLiveSnapshot";
import { buildMoldColorMap, moldColorFromMap } from "../lib/chartTheme";

const STORAGE_KEY = "tv_selected_machine_ids";
const REFRESH_MS = 30_000;
const DEFAULT_ROTATE_MS = 20_000;
const DEFAULT_IDLE_STOPPED_S = 180;
const DISPLAY_TZ = "Europe/Istanbul";
const UNDEFINED_MOLD = "Kalıp tanımlı değil";
const UNDEFINED_MOLD_COLOR = "#64748b";

type MachineRow = { id: number; name: string; enabled: boolean };

// Target-vs-actual efficiency (sliced by mold changes). efficiency_pct is null
// and available=false when the running mold has no defined target cycle.
type SlicedEfficiency = {
  actual_count: number;
  target_count: number;
  efficiency_pct: number | null;
  avg_cycle_s: number;
  target_cycle_s: number | null;
  available: boolean;
};

type ShiftHourPoint = {
  hour: number;
  label: string;
  count: number;
  by_mold: Record<string, number>;
  is_downtime?: boolean;
};

type ShiftChart = {
  id: string;
  name: string;
  date: string;
  title: string;
  start: string;
  end: string;
  is_current: boolean;
  mold_names: string[];
  hourly: ShiftHourPoint[];
};

type TvMachineData = {
  machine_id: number;
  name: string;
  window_label: string;
  active_mold_name: string | null;
  active_mold: {
    mold_id: number;
    mold_name: string | null;
    target_cycle_s: number | null;
  } | null;
  shift: { id: string; name: string };
  mold_output: { actual_count: number; mold_name: string | null; available: boolean };
  shift_output: { actual_count: number };
  shift_target_plan: { target_count: number; available: boolean };
  mold_efficiency: SlicedEfficiency;
  shift_efficiency: SlicedEfficiency;
  summary: {
    cycle_count: number;
    avg_cycle_s: number;
    min_cycle_s: number;
    max_cycle_s: number;
  };
  downtimes?: Array<{ start: string; end: string }>;
  shift_charts?: ShiftChart[];
};

function loadSelectedIds(): number[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return null;
    return parsed.map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return null;
  }
}

function saveSelectedIds(ids: number[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}

function runStatus(
  live: MachineSnap | undefined,
  idleStoppedSeconds: number,
): { running: boolean; label: string; color: string; bg: string } {
  const running = live?.idle_s != null && live.idle_s < idleStoppedSeconds;
  if (running) {
    return { running: true, label: "ÇALIŞIYOR", color: "text-emerald-300", bg: "bg-emerald-500" };
  }
  return { running: false, label: "DURUYOR", color: "text-red-300", bg: "bg-red-500" };
}

function pctColor(pct: number | null | undefined): string {
  if (pct == null) return "text-slate-400";
  if (pct >= 90) return "text-emerald-400";
  if (pct >= 70) return "text-amber-300";
  return "text-red-400";
}

function effValue(block: SlicedEfficiency): string {
  if (!block.available || block.efficiency_pct == null) return "—";
  return `%${block.efficiency_pct.toFixed(0)}`;
}

function effSubtitle(block: SlicedEfficiency, avgOverride?: number): string {
  if (!block.available) return UNDEFINED_MOLD;
  const parts = [`${block.actual_count} / ${block.target_count || "—"} (şimdiye)`];
  if (block.target_cycle_s) {
    parts.push(`Hedef ${block.target_cycle_s.toFixed(2)}s`);
  }
  const avg = avgOverride != null && avgOverride > 0 ? avgOverride : block.avg_cycle_s;
  parts.push(avg > 0 ? `Ort ${avg.toFixed(2)}s` : "Ort —");
  return parts.join(" · ");
}

function KpiCard({
  title,
  value,
  subtitle,
  accent = "text-white",
}: {
  title: string;
  value: string;
  subtitle?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-700 bg-slate-900/80 p-5 shadow-lg">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</div>
      <div className={`mt-2 text-4xl font-black tabular-nums ${accent}`}>{value}</div>
      {subtitle && <div className="mt-1 text-sm text-slate-400">{subtitle}</div>}
    </div>
  );
}

function MachineRunStatus({
  live,
  idleStoppedSeconds,
}: {
  live: MachineSnap | undefined;
  idleStoppedSeconds: number;
}) {
  const st = runStatus(live, idleStoppedSeconds);
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-slate-700 bg-slate-900/80 p-6">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Makine Durumu</div>
      <div className={`mt-4 text-5xl font-black tracking-tight ${st.color}`}>{st.label}</div>
    </div>
  );
}

function moldBarColor(colorMap: Map<string, string>, name: string): string {
  return name === UNDEFINED_MOLD ? UNDEFINED_MOLD_COLOR : moldColorFromMap(colorMap, name);
}

function ShiftProductionChart({ chart }: { chart: ShiftChart }) {
  const moldNames = chart.mold_names ?? [];
  const colorMap = useMemo(() => buildMoldColorMap(moldNames), [moldNames]);
  const data = useMemo(
    () =>
      chart.hourly.map((h) => ({
        hour: h.hour,
        label: h.label,
        count: h.count,
        is_downtime: !!h.is_downtime,
        by_mold: h.by_mold,
        ...h.by_mold,
      })),
    [chart.hourly],
  );
  const maxCount = Math.max(1, ...chart.hourly.map((h) => h.count));
  const accent = chart.is_current ? "border-emerald-800" : "border-slate-700";
  const hasDowntime = chart.hourly.some((h) => h.is_downtime);
  return (
    <div className={`flex min-h-0 flex-1 flex-col rounded-2xl border bg-slate-900/80 px-3 py-2 ${accent}`}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <div className="text-sm font-semibold text-slate-200">{chart.title}</div>
        <div className="text-xs text-slate-500">
          {chart.start}–{chart.end}
          {chart.is_current ? " · şimdi" : ""}
          {hasDowntime ? " · duruş saatleri kırmızı" : ""}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: 4 }} barCategoryGap="12%">
            <CartesianGrid strokeDasharray="2 4" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: "#64748b", fontSize: 10 }}
              interval={0}
              tickFormatter={(label: string) => {
                const row = chart.hourly.find((h) => h.label === label);
                return row?.is_downtime ? `${label}*` : label;
              }}
            />
            <YAxis hide domain={[0, maxCount * 1.15]} />
            <Tooltip
              cursor={{ fill: "#1e293b" }}
              content={({ payload }) => {
                const d = payload?.[0]?.payload as (ShiftHourPoint & { is_downtime?: boolean }) | undefined;
                if (!d) return null;
                const parts = Object.entries(d.by_mold ?? {}).filter(([, v]) => v > 0);
                return (
                  <div className="rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-200">
                    <div className="font-semibold">{d.label}</div>
                    <div>{d.count} döngü</div>
                    {d.is_downtime && <div className="text-rose-300">Duruş saati (döngüler düşüldü)</div>}
                    {parts.length > 1 &&
                      parts.map(([name, v]) => (
                        <div key={name} className="flex items-center gap-1 text-slate-400">
                          <span
                            className="inline-block h-2 w-2 rounded-sm"
                            style={{ background: moldBarColor(colorMap, name) }}
                          />
                          {name}: {v}
                        </div>
                      ))}
                  </div>
                );
              }}
            />
            {moldNames.length > 0 ? (
              moldNames.map((name, idx) => (
                <Bar
                  key={name}
                  dataKey={name}
                  stackId="h"
                  fill={moldBarColor(colorMap, name)}
                  isAnimationActive={false}
                  radius={idx === moldNames.length - 1 ? [3, 3, 0, 0] : undefined}
                >
                  {chart.hourly.map((entry) => (
                    <Cell
                      key={`${chart.date}-${chart.id}-${entry.label}-${name}`}
                      fill={
                        entry.is_downtime
                          ? "#fb7185"
                          : moldBarColor(colorMap, name)
                      }
                      fillOpacity={entry.is_downtime ? 0.55 : 1}
                    />
                  ))}
                  {idx === moldNames.length - 1 && (
                    <LabelList
                      dataKey="count"
                      position="top"
                      formatter={(value: number) => (value > 0 ? String(value) : "")}
                      style={{ fill: "#e2e8f0", fontSize: 10, fontWeight: 600 }}
                    />
                  )}
                </Bar>
              ))
            ) : (
              <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                {chart.hourly.map((entry) => (
                  <Cell
                    key={`${chart.date}-${chart.id}-${entry.label}`}
                    fill={entry.is_downtime ? "#fb7185" : "#1e293b"}
                    opacity={entry.is_downtime ? 0.55 : 0.35}
                  />
                ))}
              </Bar>
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {moldNames.length > 1 && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
          {moldNames.map((name) => (
            <span key={name} className="inline-flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-sm"
                style={{ background: moldBarColor(colorMap, name) }}
              />
              {name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function fallbackShiftChart(): ShiftChart {
  return {
    id: "daily",
    name: "Gün",
    date: "",
    title: "Üretim Grafiği",
    start: "00:00",
    end: "24:00",
    is_current: true,
    mold_names: [],
    hourly: [],
  };
}

function TvMachineScreen({
  data,
  live,
  index,
  total,
  idleStoppedSeconds,
}: {
  data: TvMachineData;
  live: MachineSnap | undefined;
  index: number;
  total: number;
  idleStoppedSeconds: number;
}) {
  const st = runStatus(live, idleStoppedSeconds);
  const shiftCharts =
    data.shift_charts && data.shift_charts.length > 0
      ? data.shift_charts
      : [fallbackShiftChart()];
  const moldDefined = data.active_mold != null;
  const moldName = data.active_mold_name || live?.mold_name || UNDEFINED_MOLD;
  const downtimeCount = data.downtimes?.length ?? 0;

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <div className="flex items-center gap-3">
            <span className={`h-4 w-4 rounded-full ${st.bg}`} />
            <h2 className="text-3xl font-bold text-white">{data.name}</h2>
            <span className={`rounded-full px-3 py-1 text-sm font-semibold ${st.color} bg-slate-800`}>
              {st.label}
            </span>
            {downtimeCount > 0 && (
              <span className="rounded-full bg-rose-950 px-3 py-1 text-sm font-semibold text-rose-300">
                {downtimeCount} duruş (verime yansıtıldı)
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-500">
            Aktif kalıp: <span className="text-amber-300">{moldName}</span>
          </p>
        </div>
        <div className="ml-auto text-right text-sm text-slate-500">
          Ekran {index + 1} / {total}
        </div>
      </div>

      <div className="grid flex-1 gap-4 lg:grid-cols-12">
        <div className="grid gap-4 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-3">
          <KpiCard
            title="Vardiya Üretim Sayısı"
            value={data.shift_output.actual_count.toLocaleString("tr-TR")}
            subtitle={`${data.shift.name} vardiyası`}
          />
          <KpiCard
            title="Kalıp Üretim Sayısı"
            value={data.mold_output.actual_count.toLocaleString("tr-TR")}
            subtitle={data.mold_output.available ? (data.mold_output.mold_name ?? "—") : UNDEFINED_MOLD}
            accent="text-sky-300"
          />
          <KpiCard
            title="Vardiya Hedef Sayısı"
            value={
              data.shift_target_plan.available
                ? data.shift_target_plan.target_count.toLocaleString("tr-TR")
                : "—"
            }
            subtitle={data.shift_target_plan.available ? undefined : UNDEFINED_MOLD}
            accent="text-sky-300"
          />
          <KpiCard
            title="Kalıp Bazlı Verimlilik"
            value={effValue(data.mold_efficiency)}
            subtitle={effSubtitle(
              data.mold_efficiency,
              data.summary.cycle_count > 0 ? data.summary.avg_cycle_s : undefined,
            )}
            accent={pctColor(data.mold_efficiency.available ? data.mold_efficiency.efficiency_pct : null)}
          />
          <KpiCard
            title="Vardiya Gerçekleşen Verimlilik"
            value={effValue(data.shift_efficiency)}
            subtitle={effSubtitle(data.shift_efficiency)}
            accent={pctColor(data.shift_efficiency.available ? data.shift_efficiency.efficiency_pct : null)}
          />
        </div>

        <div className="grid gap-4 lg:col-span-4">
          <MachineRunStatus live={live} idleStoppedSeconds={idleStoppedSeconds} />
          <div className="rounded-2xl border border-slate-700 bg-slate-900/80 p-4">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Kalıp Özeti</div>
            <div className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-400">Kalıp</span>
                <span className={`font-semibold ${moldDefined ? "text-amber-300" : "text-slate-500"}`}>
                  {moldDefined ? moldName : UNDEFINED_MOLD}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Kalıp döngüsü</span>
                <span className="font-semibold tabular-nums">{data.summary.cycle_count}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Ort. süre</span>
                <span className="font-semibold tabular-nums">
                  {data.summary.cycle_count > 0 ? `${data.summary.avg_cycle_s.toFixed(2)} s` : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Son döngü</span>
                <span className="font-semibold tabular-nums">
                  {live?.cycle_time_last != null ? `${live.cycle_time_last.toFixed(2)} s` : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Min / Max</span>
                <span className="font-semibold tabular-nums">
                  {data.summary.cycle_count > 0
                    ? `${data.summary.min_cycle_s.toFixed(2)} / ${data.summary.max_cycle_s.toFixed(2)} s`
                    : "—"}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div
        className="flex min-h-0 flex-col gap-2"
        style={{ height: `${Math.min(10 + shiftCharts.length * 5.5, 30)}rem` }}
      >
        {shiftCharts.map((chart, i) => (
          <ShiftProductionChart key={`${chart.date}-${chart.id}-${i}`} chart={chart} />
        ))}
      </div>
    </div>
  );
}

export function TvWallPage() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const { snapshot, connected } = useLiveSnapshot();
  const [allMachines, setAllMachines] = useState<MachineRow[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>(() => loadSelectedIds() ?? []);
  const [machineData, setMachineData] = useState<Map<number, TvMachineData>>(new Map());
  const [setupOpen, setSetupOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [clock, setClock] = useState(() => new Date());
  const [activeIndex, setActiveIndex] = useState(0);
  const [rotateMs, setRotateMs] = useState(DEFAULT_ROTATE_MS);
  const [idleStoppedSeconds, setIdleStoppedSeconds] = useState(DEFAULT_IDLE_STOPPED_S);

  useEffect(() => {
    apiGet<{ tv_rotate_seconds: number; idle_stopped_seconds?: number }>("/api/settings/production")
      .then((cfg) => {
        const sec = Number(cfg.tv_rotate_seconds);
        if (Number.isFinite(sec) && sec >= 5) setRotateMs(sec * 1000);
        const idle = Number(cfg.idle_stopped_seconds);
        if (Number.isFinite(idle) && idle >= 30) setIdleStoppedSeconds(idle);
      })
      .catch(() => {
        setRotateMs(DEFAULT_ROTATE_MS);
        setIdleStoppedSeconds(DEFAULT_IDLE_STOPPED_S);
      });
  }, []);

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!user) {
      setAllMachines([]);
      return;
    }
    apiGet<MachineRow[]>("/api/machines")
      .then((rows) => setAllMachines(rows.filter((m) => m.enabled)))
      .catch(() => setAllMachines([]));
  }, [user]);

  const displayIds = useMemo(() => {
    if (selectedIds.length > 0) return selectedIds;
    return allMachines.map((m) => m.id);
  }, [selectedIds, allMachines]);

  const loadBoard = useCallback(async () => {
    if (displayIds.length === 0) {
      setMachineData(new Map());
      return;
    }
    try {
      const results = await Promise.all(
        displayIds.map((id) => apiGet<TvMachineData>(`/api/analytics/tv_machine?machine_id=${id}`)),
      );
      const map = new Map<number, TvMachineData>();
      results.forEach((row) => map.set(row.machine_id, row));
      setMachineData(map);
      setLastRefresh(new Date());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [displayIds]);

  useEffect(() => {
    void loadBoard();
    const t = setInterval(() => void loadBoard(), REFRESH_MS);
    return () => clearInterval(t);
  }, [loadBoard]);

  useEffect(() => {
    if (displayIds.length <= 1) return;
    const t = setInterval(() => {
      setActiveIndex((i) => (i + 1) % displayIds.length);
    }, rotateMs);
    return () => clearInterval(t);
  }, [displayIds.length, rotateMs]);

  useEffect(() => {
    setActiveIndex(0);
  }, [displayIds.join(",")]);

  const liveById = useMemo(
    () => new Map(snapshot.machines.map((m) => [m.id, m])),
    [snapshot.machines],
  );

  const currentId = displayIds[activeIndex];
  const currentData = currentId ? machineData.get(currentId) : undefined;

  const clockStr = clock.toLocaleString("tr-TR", {
    timeZone: DISPLAY_TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  function toggleId(id: number) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].sort((a, b) => a - b),
    );
  }

  function saveSetup() {
    saveSelectedIds(selectedIds);
    setSetupOpen(false);
    void apiPost("/api/activity", { action: "tv.machines.select" }).catch(() => {});
    void loadBoard();
  }

  function openSetup() {
    if (!user) {
      navigate("/login", { state: { from: "/tv" } });
      return;
    }
    setSetupOpen(true);
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center gap-4 border-b border-slate-800 px-6 py-3">
        <div>
          <h1 className="text-xl font-bold text-emerald-400">Bilgi Ekranı</h1>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-4 text-sm">
          <span className="tabular-nums text-base text-slate-300">{clockStr}</span>
          <span className="text-xs">
            WS:{" "}
            {connected ? <span className="text-emerald-400">bağlı</span> : <span className="text-red-400">kopuk</span>}
          </span>
          {lastRefresh && (
            <span className="text-xs text-slate-600">
              {lastRefresh.toLocaleTimeString("tr-TR", { timeZone: DISPLAY_TZ })}
            </span>
          )}
          {!authLoading && user ? (
            <button
              type="button"
              className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
              onClick={openSetup}
            >
              Makineleri seç
            </button>
          ) : !authLoading ? (
            <Link
              to="/login"
              state={{ from: "/tv" }}
              className="rounded-lg bg-sky-800 px-3 py-1.5 text-sm text-sky-100 hover:bg-sky-700"
            >
              Giriş yap (makine seç)
            </Link>
          ) : null}
          {displayIds.length > 1 && (
            <div className="flex gap-1">
              {displayIds.map((id, i) => (
                <button
                  key={id}
                  type="button"
                  className={`h-2.5 w-2.5 rounded-full ${i === activeIndex ? "bg-emerald-400" : "bg-slate-700"}`}
                  onClick={() => setActiveIndex(i)}
                  aria-label={`Makine ${i + 1}`}
                />
              ))}
            </div>
          )}
          {user && <span className="text-xs text-slate-500">{user.display_name}</span>}
          <Link to="/" className="text-xs text-slate-600 hover:text-slate-400">
            ← Pano
          </Link>
        </div>
      </header>

      {err && (
        <p className="border-b border-red-900 bg-red-950/40 px-6 py-2 text-sm text-red-400">{err}</p>
      )}

      <main className="flex-1 overflow-hidden">
        {displayIds.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-xl text-slate-500">
            <span>Bilgi ekranı için makine seçilmedi</span>
            <span className="text-sm text-slate-600">
              Giriş yapıp &quot;Makineleri seç&quot; ile bu cihazda kaydedin; bilgi ekranı şifresiz açık kalır
            </span>
          </div>
        ) : currentData ? (
          <TvMachineScreen
            data={currentData}
            live={liveById.get(currentData.machine_id)}
            index={activeIndex}
            total={displayIds.length}
            idleStoppedSeconds={idleStoppedSeconds}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-500">Yükleniyor…</div>
        )}
      </main>

      {setupOpen && user && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-h-[85vh] w-full max-w-lg overflow-auto rounded-2xl border border-slate-600 bg-slate-900 p-6 shadow-2xl">
            <h2 className="mb-2 text-xl font-semibold">Bilgi ekranında gösterilecek makineler</h2>
            <p className="mb-4 text-sm text-slate-400">
              Her makine tam ekran gösterilir ve ayarlardaki sürede otomatik döner. Seçim bu tarayıcıda saklanır.
            </p>
            <div className="mb-4 space-y-2">
              {allMachines.map((m) => (
                <label
                  key={m.id}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-700 px-3 py-3 hover:bg-slate-800"
                >
                  <input
                    type="checkbox"
                    className="h-5 w-5"
                    checked={selectedIds.includes(m.id)}
                    onChange={() => toggleId(m.id)}
                  />
                  <span className="text-lg">
                    {m.name} <span className="text-slate-500">#{m.id}</span>
                  </span>
                </label>
              ))}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="flex-1 rounded-lg bg-sky-600 py-3 font-semibold text-white hover:bg-sky-500"
                onClick={saveSetup}
              >
                Kaydet
              </button>
              <button
                type="button"
                className="rounded-lg bg-slate-700 px-4 py-3 hover:bg-slate-600"
                onClick={() => setSelectedIds(allMachines.map((m) => m.id))}
              >
                Tümü
              </button>
              <button
                type="button"
                className="rounded-lg bg-slate-700 px-4 py-3 hover:bg-slate-600"
                onClick={() => setSetupOpen(false)}
              >
                İptal
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
