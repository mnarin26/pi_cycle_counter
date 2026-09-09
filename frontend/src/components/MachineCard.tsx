import type { MachineSnap } from "../hooks/useLiveSnapshot";
import { Link } from "react-router-dom";
import { DEFAULT_IDLE_STOPPED_S, machineStatusView } from "../lib/machineStatus";

export function MachineCard({
  m,
  canAssign,
  onAssign,
  idleStoppedSeconds = DEFAULT_IDLE_STOPPED_S,
}: {
  m: MachineSnap;
  canAssign?: boolean;
  onAssign?: (machine: MachineSnap) => void;
  idleStoppedSeconds?: number;
}) {
  const status = machineStatusView(m, idleStoppedSeconds);
  return (
    <div className="rounded-lg border border-slate-700 bg-panel2 p-4 min-w-[200px] shadow-sm hover:border-slate-500">
      <Link to={`/machines/${m.id}`} className="block" title={`${m.name} detay`}>
        <div className="text-slate-400 text-sm">{m.name}</div>
        <div className={`text-xl font-bold ${status.colorClass}`}>{status.label}</div>
        <div className="mt-2 text-sm text-slate-300 space-y-1">
          <div>Döngü: {m.cycle_time_last != null ? `${m.cycle_time_last.toFixed(2)}s` : "—"}</div>
          <div>Kalıp: {m.mold_name || "—"}</div>
          <div>Poz: {m.position_01 != null ? m.position_01.toFixed(2) : "—"}</div>
          <div className="text-xs text-slate-500">Güven: {((m.confidence ?? 0) * 100).toFixed(0)}%</div>
        </div>
      </Link>
      {canAssign && onAssign && (
        <button
          type="button"
          className="mt-3 w-full rounded-md bg-sky-700 px-3 py-2 text-sm font-semibold text-white hover:bg-sky-600"
          onClick={() => onAssign(m)}
        >
          Kalıp ata
        </button>
      )}
    </div>
  );
}
