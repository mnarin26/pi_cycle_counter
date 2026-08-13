import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useLiveSnapshot, type MachineSnap } from "../hooks/useLiveSnapshot";

const STORAGE_KEY = "tv_selected_machine_ids";
const REFRESH_MS = 30_000;
const DEFAULT_ROTATE_MS = 20_000;
const DISPLAY_TZ = "Europe/Istanbul";

type MachineRow = { id: number; name: string; enabled: boolean };

type EfficiencyBlock = {
  actual_count: number;
  target_count: number;
  realization_pct: number;
  performance_pct: number;
  efficiency_pct: number;
  avg_cycle_s: number;
  target_cycle_s: number | null;
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
    daily_target_count: number;
  } | null;
  daily: EfficiencyBlock;
  shift: EfficiencyBlock & { id: string; name: string };
  summary: {
    cycle_count: number;
    avg_cycle_s: number;
    min_cycle_s: number;
    max_cycle_s: number;
  };
  hourly: Array<{ hour: number; count: number }>;
};

function istanbulHourNow(): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    hour: "numeric",
    hour12: false,
  }).formatToParts(new Date());
  return Number(parts.find((p) => p.type === "hour")?.value ?? 0);
}

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

function stateInfo(state: string): { label: string; color: string; bg: string } {
  switch (state) {
    case "OPEN":
      return { label: "ÇALIŞIYOR", color: "text-emerald-300", bg: "bg-emerald-500" };
    case "CLOSED":
      return { label: "KAPALI", color: "text-sky-300", bg: "bg-sky-500" };
    case "MOVING":
      return { label: "HAREKET", color: "text-amber-300", bg: "bg-amber-400" };
    default:
      return { label: state || "—", color: "text-slate-300", bg: "bg-slate-500" };
  }
}

function fillHours(hourly: Array<{ hour: number; count: number }>): Array<{ hour: number; count: number }> {
  const map = new Map(hourly.map((h) => [h.hour, h.count]));
  const nowHour = istanbulHourNow();
  return Array.from({ length: 24 }, (_, h) => ({ hour: h, count: map.get(h) ?? 0 })).filter(
    (h) => h.hour <= nowHour,
  );
}

function hourLabel(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

function pctColor(pct: number): string {
  if (pct >= 90) return "text-emerald-400";
  if (pct >= 70) return "text-amber-300";
  return "text-red-400";
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

function statusFill(state: string): string {
  switch (state) {
    case "OPEN":
      return "#34d399";
    case "CLOSED":
      return "#38bdf8";
    case "MOVING":
      return "#fbbf24";
    default:
      return "#64748b";
  }
}

function MachineStatusDonut({ state }: { state: string }) {
  const st = stateInfo(state);
  const data = [{ name: st.label, value: 1, fill: statusFill(state) }];
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-slate-700 bg-slate-900/80 p-4">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Makine Durumu</div>
      <div className="h-36 w-36">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" innerRadius={48} outerRadius={68} stroke="none" />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className={`text-xl font-bold ${st.color}`}>{st.label}</div>
    </div>
  );
}

function TvMachineScreen({
  data,
  live,
  index,
  total,
}: {
  data: TvMachineData;
  live: MachineSnap | undefined;
  index: number;
  total: number;
}) {
  const st = stateInfo(live?.state ?? "—");
  const chartData = fillHours(data.hourly);
  const maxCount = Math.max(1, ...chartData.map((h) => h.count));
  const moldName = data.active_mold_name || live?.mold_name || "—";

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
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {data.window_label} · Aktif kalıp: <span className="text-amber-300">{moldName}</span>
          </p>
        </div>
        <div className="ml-auto text-right text-sm text-slate-500">
          Ekran {index + 1} / {total}
        </div>
      </div>

      <div className="grid flex-1 gap-4 lg:grid-cols-12">
        <div className="grid gap-4 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-3">
          <KpiCard
            title="Günlük Üretim"
            value={data.daily.actual_count.toLocaleString("tr-TR")}
            subtitle="Gerçekleşen baskı"
          />
          <KpiCard
            title="Hedef Üretim"
            value={data.daily.target_count.toLocaleString("tr-TR")}
            subtitle="Günlük hedef"
            accent="text-sky-300"
          />
          <KpiCard
            title="Gerçekleşme Oranı"
            value={`%${data.daily.realization_pct.toFixed(0)}`}
            subtitle={`${data.daily.actual_count} / ${data.daily.target_count || "—"}`}
            accent={pctColor(data.daily.realization_pct)}
          />
          <KpiCard
            title={`${data.shift.name} Vardiyası`}
            value={data.shift.actual_count.toLocaleString("tr-TR")}
            subtitle={`Hedef: ${data.shift.target_count.toLocaleString("tr-TR")}`}
          />
          <KpiCard
            title="Vardiya Gerçekleşme"
            value={`%${data.shift.realization_pct.toFixed(0)}`}
            subtitle="Vardiya bazlı"
            accent={pctColor(data.shift.realization_pct)}
          />
          <KpiCard
            title="Verimlilik"
            value={`%${data.daily.efficiency_pct.toFixed(0)}`}
            subtitle={
              data.daily.target_cycle_s
                ? `Hedef ${data.daily.target_cycle_s.toFixed(2)}s · Ort ${data.daily.avg_cycle_s.toFixed(2)}s`
                : "Kalıp hedef süresi tanımlı değil"
            }
            accent={pctColor(data.daily.efficiency_pct)}
          />
        </div>

        <div className="grid gap-4 lg:col-span-4">
          <MachineStatusDonut state={live?.state ?? "—"} />
          <div className="rounded-2xl border border-slate-700 bg-slate-900/80 p-4">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Kalıp Özeti</div>
            <div className="mt-3 space-y-2 text-sm">
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
              <div className="flex justify-between">
                <span className="text-slate-400">Performans</span>
                <span className={`font-semibold tabular-nums ${pctColor(data.daily.performance_pct)}`}>
                  %{data.daily.performance_pct.toFixed(0)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="h-40 rounded-2xl border border-slate-700 bg-slate-900/80 p-3">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-500">Üretim Grafiği</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={chartData} margin={{ top: 12, right: 8, bottom: 0, left: 4 }} barCategoryGap="12%">
            <CartesianGrid strokeDasharray="2 4" stroke="#1e293b" vertical={false} />
            <XAxis dataKey="hour" tickFormatter={hourLabel} tick={{ fill: "#64748b", fontSize: 11 }} interval={2} />
            <YAxis hide domain={[0, maxCount * 1.15]} />
            <Tooltip
              cursor={{ fill: "#1e293b" }}
              content={({ payload, label }) => {
                const d = payload?.[0]?.payload as { hour: number; count: number } | undefined;
                if (!d) return null;
                return (
                  <div className="rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-200">
                    <div className="font-semibold">{hourLabel(Number(label))}</div>
                    <div>{d.count} döngü</div>
                  </div>
                );
              }}
            />
            <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {chartData.map((entry) => (
                <Cell
                  key={entry.hour}
                  fill={entry.count > 0 ? "#22c55e" : "#1e293b"}
                  opacity={entry.count > 0 ? 0.9 : 0.35}
                />
              ))}
              <LabelList
                dataKey="count"
                position="top"
                formatter={(value: number) => (value > 0 ? String(value) : "")}
                style={{ fill: "#e2e8f0", fontSize: 10, fontWeight: 600 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
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

  useEffect(() => {
    apiGet<{ tv_rotate_seconds: number }>("/api/settings/production")
      .then((cfg) => {
        const sec = Number(cfg.tv_rotate_seconds);
        if (Number.isFinite(sec) && sec >= 5) setRotateMs(sec * 1000);
      })
      .catch(() => setRotateMs(DEFAULT_ROTATE_MS));
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
          <h1 className="text-xl font-bold text-emerald-400">Üretim TV</h1>
          <p className="text-xs text-slate-500">Tam ekran makine özeti · {displayIds.length} makine</p>
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
            <span>TV için makine seçilmedi</span>
            <span className="text-sm text-slate-600">
              Giriş yapıp &quot;Makineleri seç&quot; ile bu cihazda kaydedin; TV şifresiz açık kalır
            </span>
          </div>
        ) : currentData ? (
          <TvMachineScreen
            data={currentData}
            live={liveById.get(currentData.machine_id)}
            index={activeIndex}
            total={displayIds.length}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-500">Yükleniyor…</div>
        )}
      </main>

      {setupOpen && user && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-h-[85vh] w-full max-w-lg overflow-auto rounded-2xl border border-slate-600 bg-slate-900 p-6 shadow-2xl">
            <h2 className="mb-2 text-xl font-semibold">TV&apos;de gösterilecek makineler</h2>
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
