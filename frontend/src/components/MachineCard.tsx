import type { MachineSnap } from "../hooks/useLiveSnapshot";
import { Link } from "react-router-dom";

function stateColor(s: string) {
  if (s === "OPEN") return "text-ok";
  if (s === "CLOSED") return "text-accent";
  if (s === "MOVING") return "text-amber-300";
  if (s === "DISABLED") return "text-slate-500";
  return "text-slate-400";
}

export function MachineCard({ m }: { m: MachineSnap }) {
  return (
    <Link
      to={`/machines/${m.id}`}
      className="block rounded-lg border border-slate-700 bg-panel2 p-4 min-w-[200px] shadow-sm hover:border-slate-500"
      title={`${m.name} detay`}
    >
      <div className="text-slate-400 text-sm">{m.name}</div>
      <div className={`text-xl font-bold ${stateColor(m.state)}`}>{m.state}</div>
      <div className="mt-2 text-sm text-slate-300 space-y-1">
        <div>Döngü: {m.cycle_time_last != null ? `${m.cycle_time_last.toFixed(2)}s` : "—"}</div>
        <div>Kalıp: {m.mold_name || "—"}</div>
        <div>Poz: {m.position_01 != null ? m.position_01.toFixed(2) : "—"}</div>
        <div className="text-xs text-slate-500">Güven: {(m.confidence * 100).toFixed(0)}%</div>
      </div>
    </Link>
  );
}
