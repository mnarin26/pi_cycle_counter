import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet } from "../api/client";
import { useLiveSnapshot, type MachineSnap } from "../hooks/useLiveSnapshot";

const STORAGE_KEY = "tv_selected_machine_ids";
const REFRESH_MS = 45_000;
const DISPLAY_TZ = "Europe/Istanbul";

function istanbulHourNow(): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    hour: "numeric",
    hour12: false,
  }).formatToParts(new Date());
  return Number(parts.find((p) => p.type === "hour")?.value ?? 0);
}

type MachineRow = { id: number; name: string; enabled: boolean };

type TvSummary = {
  cycle_count: number;
  avg_cycle_s: number;
  min_cycle_s: number;
  max_cycle_s: number;
};

type HourBucket = { hour: number; count: number; avg_cycle_s: number };

type TvBoardMachine = {
  machine_id: number;
  name: string;
  total_cycle_count: number;
  active_mold_id: number | null;
  active_mold_name: string | null;
  summary: TvSummary;
  hourly: HourBucket[];
};

type TvBoard = {
  window_label: string;
  machines: TvBoardMachine[];
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

function stateInfo(state: string): { label: string; border: string; dot: string } {
  switch (state) {
    case "OPEN":
      return { label: "AÇIK", border: "border-emerald-500", dot: "bg-emerald-400" };
    case "CLOSED":
      return { label: "KAPALI", border: "border-sky-500", dot: "bg-sky-400" };
    case "MOVING":
      return { label: "HAREKET", border: "border-amber-400", dot: "bg-amber-300" };
    default:
      return { label: state || "—", border: "border-slate-600", dot: "bg-slate-500" };
  }
}

/** Fill 0–23 Istanbul hours from server data; show through current hour. */
function fillHours(hourly: HourBucket[]): HourBucket[] {
  const map = new Map(hourly.map((h) => [h.hour, h]));
  const nowHour = istanbulHourNow();
  return Array.from({ length: 24 }, (_, h) => map.get(h) ?? { hour: h, count: 0, avg_cycle_s: 0 }).filter(
    (h) => h.hour <= nowHour,
  );
}

function hourLabel(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

function TvMachineRow({
  board,
  live,
}: {
  board: TvBoardMachine;
  live: MachineSnap | undefined;
}) {
  const st = stateInfo(live?.state ?? "—");
  const s = board.summary;
  const chartData = fillHours(board.hourly);
  const maxCount = Math.max(1, ...chartData.map((h) => h.count));
  const moldName = board.active_mold_name || live?.mold_name || "—";
  const hasMoldStats = s.cycle_count > 0;

  return (
    <article className={`rounded-2xl border-2 ${st.border} bg-slate-900 overflow-hidden`}>
      {/* ── Top info bar ── */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 px-5 py-4">
        {/* Name + state */}
        <div className="flex items-center gap-3 min-w-[180px]">
          <span className={`h-3 w-3 rounded-full shrink-0 ${st.dot}`} />
          <span className="text-2xl font-bold text-white">{board.name}</span>
          <span className="rounded-full bg-slate-800 px-3 py-0.5 text-base font-semibold text-slate-200">
            {st.label}
          </span>
        </div>

        {/* Daily total count */}
        <div className="flex items-baseline gap-2">
          <span className="text-4xl font-black tabular-nums text-white">
            {board.total_cycle_count.toLocaleString("tr-TR")}
          </span>
          <span className="text-base text-slate-400">döngü bugün</span>
        </div>

        {/* Active mold + its stats */}
        <div className="flex flex-wrap gap-6 text-base">
          <Stat label="Aktif kalıp" value={moldName} color="text-amber-300" />
          <Stat
            label="Kalıp döngüsü"
            value={hasMoldStats ? s.cycle_count.toLocaleString("tr-TR") : "—"}
            color="text-slate-200"
          />
          <Stat
            label="Ort. süre"
            value={hasMoldStats ? `${s.avg_cycle_s.toFixed(2)} s` : "—"}
            color="text-sky-300"
          />
          <Stat
            label="Son döngü"
            value={live?.cycle_time_last != null ? `${live.cycle_time_last.toFixed(2)} s` : "—"}
            color="text-sky-300"
          />
          <Stat
            label="Min / Max"
            value={hasMoldStats ? `${s.min_cycle_s.toFixed(2)} / ${s.max_cycle_s.toFixed(2)} s` : "—"}
            color="text-slate-300"
          />
        </div>
      </div>

      {/* ── Hourly bar chart ── */}
      <div className="h-32 border-t border-slate-800 px-2 pt-1 pb-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 16, right: 4, bottom: 0, left: 4 }} barCategoryGap="10%">
            <CartesianGrid strokeDasharray="2 4" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="hour"
              tickFormatter={hourLabel}
              tick={{ fill: "#64748b", fontSize: 10 }}
              interval={2}
            />
            <YAxis hide domain={[0, maxCount * 1.1]} />
            <Tooltip
              cursor={{ fill: "#1e293b" }}
              content={({ payload, label }) => {
                const d = payload?.[0]?.payload as HourBucket | undefined;
                if (!d) return null;
                return (
                  <div className="rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-200">
                    <div className="font-semibold">{hourLabel(Number(label))}</div>
                    <div>{d.count} döngü</div>
                    {d.avg_cycle_s > 0 && <div>Ort: {d.avg_cycle_s.toFixed(2)} s</div>}
                  </div>
                );
              }}
            />
            <ReferenceLine y={0} stroke="#334155" />
            <Bar dataKey="count" radius={[2, 2, 0, 0]} isAnimationActive={false}>
              {chartData.map((entry) => (
                <Cell
                  key={entry.hour}
                  fill={entry.count > 0 ? "#38bdf8" : "#1e293b"}
                  opacity={entry.count > 0 ? 0.85 : 0.4}
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
    </article>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

export function TvWallPage() {
  const { snapshot, connected } = useLiveSnapshot();
  const [allMachines, setAllMachines] = useState<MachineRow[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [board, setBoard] = useState<TvBoard | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [clock, setClock] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    apiGet<MachineRow[]>("/api/machines")
      .then((rows) => {
        const enabled = rows.filter((m) => m.enabled);
        setAllMachines(enabled);
        const saved = loadSelectedIds();
        if (saved?.length) {
          setSelectedIds(saved.filter((id) => enabled.some((m) => m.id === id)));
        } else {
          setSelectedIds(enabled.map((m) => m.id));
        }
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const loadBoard = useCallback(async () => {
    if (!selectedIds.length) {
      setBoard({ window_label: "Makine seçilmedi", machines: [] });
      return;
    }
    try {
      const data = await apiGet<TvBoard>(
        `/api/analytics/tv_board?machine_ids=${selectedIds.join(",")}`,
      );
      setBoard(data);
      setLastRefresh(new Date());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [selectedIds]);

  useEffect(() => {
    if (!selectedIds.length) return;
    void loadBoard();
    const t = setInterval(() => void loadBoard(), REFRESH_MS);
    return () => clearInterval(t);
  }, [loadBoard, selectedIds]);

  const liveById = useMemo(
    () => new Map(snapshot.machines.map((m) => [m.id, m])),
    [snapshot.machines],
  );

  const tiles = useMemo<TvBoardMachine[]>(() => {
    if (!board) return [];
    const order = new Map(selectedIds.map((id, i) => [id, i]));
    return [...board.machines].sort(
      (a, b) => (order.get(a.machine_id) ?? 0) - (order.get(b.machine_id) ?? 0),
    );
  }, [board, selectedIds]);

  const clockStr = clock.toLocaleString("tr-TR", {
    timeZone: "Europe/Istanbul",
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

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Header */}
      <header className="flex flex-wrap items-center gap-4 border-b border-slate-800 px-6 py-3">
        <div>
          <h1 className="text-xl font-bold text-sky-400">Üretim TV</h1>
          <p className="text-xs text-slate-500">{board?.window_label ?? "Bugün · canlı özet"}</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-4 text-sm">
          <span className="tabular-nums text-base text-slate-300">{clockStr}</span>
          <span className="text-xs">
            WS:{" "}
            {connected ? (
              <span className="text-emerald-400">bağlı</span>
            ) : (
              <span className="text-red-400">kopuk</span>
            )}
          </span>
          {lastRefresh && (
            <span className="text-xs text-slate-600">
              {lastRefresh.toLocaleTimeString("tr-TR", { timeZone: "Europe/Istanbul" })}
            </span>
          )}
          <button
            type="button"
            className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
            onClick={() => setSetupOpen(true)}
          >
            Makineleri seç
          </button>
          <Link to="/" className="text-xs text-slate-600 hover:text-slate-400">
            ← Pano
          </Link>
        </div>
      </header>

      {err && (
        <p className="px-6 py-2 text-sm text-red-400 bg-red-950/40 border-b border-red-900">{err}</p>
      )}

      {/* Machine rows */}
      <main className="flex-1 flex flex-col gap-4 p-4 overflow-auto">
        {tiles.length === 0 ? (
          <div className="flex flex-1 items-center justify-center text-slate-500 text-xl">
            TV için en az bir makine seçin
          </div>
        ) : (
          tiles.map((t) => (
            <TvMachineRow key={t.machine_id} board={t} live={liveById.get(t.machine_id)} />
          ))
        )}
      </main>

      {/* Setup modal */}
      {setupOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-h-[85vh] w-full max-w-lg overflow-auto rounded-2xl border border-slate-600 bg-slate-900 p-6 shadow-2xl">
            <h2 className="text-xl font-semibold mb-2">TV'de gösterilecek makineler</h2>
            <p className="text-sm text-slate-400 mb-4">Seçim tarayıcıda saklanır. F11 ile tam ekran yap.</p>
            <div className="space-y-2 mb-4">
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
