import { useEffect, useMemo, useState } from "react";
import { MachineCard } from "../components/MachineCard";
import { apiGet, apiPost } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useLiveSnapshot, type MachineSnap } from "../hooks/useLiveSnapshot";
import { DEFAULT_IDLE_STOPPED_S } from "../lib/machineStatus";
import { datetimeLocalInputToUtcIso } from "../lib/chartTheme";

type MoldRow = {
  id: number;
  name: string | null;
  qr_code: string | null;
  status: string;
  mount_minutes: number | null;
  removal_minutes: number | null;
  assigned_machine_id: number | null;
  assigned_machine_name: string | null;
};

function parseApiError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* ignore */
  }
  return raw || "Atama başarısız";
}

function AssignMoldModal({
  machine,
  onClose,
}: {
  machine: MachineSnap;
  onClose: () => void;
}) {
  const [molds, setMolds] = useState<MoldRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [assignedAt, setAssignedAt] = useState("");
  const [coStart, setCoStart] = useState("");
  const [coEnd, setCoEnd] = useState("");

  useEffect(() => {
    let active = true;
    apiGet<MoldRow[]>("/api/molds")
      .then((rows) => {
        if (!active) return;
        const usable = rows.filter((m) => m.status !== "ignored");
        setMolds(usable);
        const current = usable.find((m) => m.assigned_machine_id === machine.id);
        if (current) setSelectedId(current.id);
      })
      .catch((e) => {
        if (active) setErr(parseApiError(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [machine.id]);

  const selected = molds.find((m) => m.id === selectedId);
  const takenElsewhere =
    selected &&
    selected.assigned_machine_id != null &&
    selected.assigned_machine_id !== machine.id;
  const currentMold = molds.find((m) => m.assigned_machine_id === machine.id) ?? null;

  const standardMinutes = useMemo(() => {
    if (!selected) return 0;
    const removal =
      currentMold && currentMold.id !== selected.id ? currentMold.removal_minutes ?? 0 : 0;
    const mount = selected.mount_minutes ?? 0;
    return removal + mount;
  }, [selected, currentMold]);

  const customChangeover = coStart !== "" || coEnd !== "";

  async function submit() {
    if (typeof selectedId !== "number") {
      setErr("Kalip secin");
      return;
    }
    if (customChangeover && (coStart === "" || coEnd === "")) {
      setErr("Kalıp değişimi için başlangıç ve bitişi birlikte girin");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { machine_id: machine.id };
      if (assignedAt) body.assigned_at = datetimeLocalInputToUtcIso(assignedAt);
      if (customChangeover) {
        body.changeover_start = datetimeLocalInputToUtcIso(coStart);
        body.changeover_end = datetimeLocalInputToUtcIso(coEnd);
      }
      await apiPost(`/api/molds/${selectedId}/assign`, body);
      onClose();
    } catch (e) {
      setErr(parseApiError(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-600 bg-slate-900 p-6 shadow-2xl">
        <h2 className="text-lg font-semibold text-white">Kalıp ata</h2>
        <p className="mt-1 text-sm text-slate-400">
          {machine.name} için kalıp seçin. Bir kalıp aynı anda yalnızca bir makinede çalışır.
        </p>
        {loading ? (
          <p className="mt-4 text-sm text-slate-500">Kalıplar yükleniyor…</p>
        ) : (
          <label className="mt-4 block text-sm text-slate-300">
            Kalıp
            <select
              className="mt-1 w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-white"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">Seçin…</option>
              {molds.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name || `Kalıp #${m.id}`}
                  {m.qr_code ? ` · ${m.qr_code}` : ""}
                  {m.assigned_machine_name ? ` (${m.assigned_machine_name})` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {takenElsewhere && (
          <p className="mt-3 rounded-md border border-amber-700 bg-amber-950/50 px-3 py-2 text-sm text-amber-200">
            Bu kalıp şu an {selected.assigned_machine_name || `makine #${selected.assigned_machine_id}`} üzerinde.
            Atarsanız oradan kalkar.
          </p>
        )}

        <label className="mt-4 block text-sm text-slate-300">
          Atama saati
          <input
            type="datetime-local"
            className="mt-1 w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-white"
            value={assignedAt}
            onChange={(e) => setAssignedAt(e.target.value)}
          />
          <span className="mt-1 block text-xs text-slate-500">
            Boş bırakılırsa şimdi. Geçmişe dönük atama için geçmiş bir saat seçin (grafik ve
            verimlilik yeniden hesaplanır).
          </span>
        </label>

        <div className="mt-4 rounded-md border border-slate-700 bg-slate-800/50 p-3">
          <div className="text-sm font-medium text-slate-200">Kalıp değişim süresi</div>
          {customChangeover ? (
            <p className="mt-1 text-xs text-slate-400">
              Girdiğiniz aralık kalıp değişimi olarak işlenecek.
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-400">
              Boş bırakılırsa kalıp tanımına göre toplam{" "}
              <span className="font-semibold text-slate-200">{standardMinutes} dk</span> düşülecek
              (atama saatinde biten değişim).
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <label className="text-xs text-slate-400">
              <span className="mb-1 block">Değişim başlangıcı</span>
              <input
                type="datetime-local"
                className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1.5 text-white"
                value={coStart}
                onChange={(e) => setCoStart(e.target.value)}
              />
            </label>
            <label className="text-xs text-slate-400">
              <span className="mb-1 block">Değişim bitişi</span>
              <input
                type="datetime-local"
                className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1.5 text-white"
                value={coEnd}
                onChange={(e) => setCoEnd(e.target.value)}
              />
            </label>
          </div>
        </div>

        {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
        <div className="mt-5 flex gap-2">
          <button
            type="button"
            className="flex-1 rounded-lg bg-sky-600 py-2.5 font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            disabled={saving || loading || typeof selectedId !== "number"}
            onClick={() => void submit()}
          >
            {saving ? "Atanıyor…" : "Ata"}
          </button>
          <button
            type="button"
            className="rounded-lg bg-slate-700 px-4 py-2.5 text-slate-200 hover:bg-slate-600"
            onClick={onClose}
            disabled={saving}
          >
            İptal
          </button>
        </div>
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { snapshot } = useLiveSnapshot();
  const { user } = useAuth();
  const [assignFor, setAssignFor] = useState<MachineSnap | null>(null);
  const [idleStoppedSeconds, setIdleStoppedSeconds] = useState(DEFAULT_IDLE_STOPPED_S);
  const canAssign = !!(user?.is_super || user?.permissions?.bot_mold_assign);
  const activeMachines = (snapshot.machines ?? []).filter((m) => m.state !== "DISABLED");

  useEffect(() => {
    apiGet<{ idle_stopped_seconds?: number }>("/api/settings/production")
      .then((cfg) => {
        const idle = Number(cfg.idle_stopped_seconds);
        if (Number.isFinite(idle) && idle >= 30) setIdleStoppedSeconds(idle);
      })
      .catch(() => setIdleStoppedSeconds(DEFAULT_IDLE_STOPPED_S));
  }, []);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Pano</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {activeMachines.map((m) => (
          <MachineCard
            key={m.id}
            m={m}
            canAssign={canAssign}
            onAssign={setAssignFor}
            idleStoppedSeconds={idleStoppedSeconds}
          />
        ))}
      </div>
      {assignFor && <AssignMoldModal machine={assignFor} onClose={() => setAssignFor(null)} />}
    </div>
  );
}
