import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

type Machine = {
  id: number;
  name: string;
  enabled: boolean;
  camera_id: number;
  threshold_mode: string;
  threshold_min: number;
  threshold_max: number;
  threshold_offset: number;
  line_thickness: number;
  reflector_len_min?: number | null;
  reflector_len_max?: number | null;
};

export function MachinesPage() {
  const [rows, setRows] = useState<Machine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const { snapshot } = useLiveSnapshot();

  async function load() {
    try {
      const all = await apiGet<Machine[]>("/api/machines");
      setRows(all.filter((m) => m.enabled));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  const snapById = new Map(snapshot.machines.filter((m) => m.state !== "DISABLED").map((m) => [m.id, m]));

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Makineler</h2>
      {err && <p className="text-alarm text-sm">{err}</p>}
      <div className="overflow-x-auto rounded border border-slate-700">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-left">
            <tr>
              <th className="p-3">ID</th>
              <th className="p-3">Ad</th>
              <th className="p-3">Kamera</th>
              <th className="p-3">Eşik</th>
              <th className="p-3">Sinyal (peak/bg)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id} className="border-t border-slate-700">
                <td className="p-3">{m.id}</td>
                <td className="p-3">{m.name}</td>
                <td className="p-3">{m.camera_id}</td>
                <td className="p-3">
                  {(() => {
                    const sm = snapById.get(m.id);
                    const mode = (m.threshold_mode || "fixed").toLowerCase();
                    const off = Number(m.threshold_offset || 0);
                    const offTxt = off === 0 ? "" : ` (off ${off >= 0 ? "+" : ""}${off})`;
                    const amin = sm?.threshold_active_min;
                    if (mode === "adaptive" || mode === "learned") {
                      return amin != null ? `adp prom≥${amin}${offTxt}` : "adp:—";
                    }
                    return `prom≥${m.threshold_min}${offTxt}`;
                  })()}
                </td>
                <td className="p-3">
                  {(() => {
                    const sm = snapById.get(m.id);
                    if (!sm) return "—";
                    const peak = sm.peak ?? 0;
                    const bg = sm.background ?? 0;
                    const prom = sm.prominence ?? 0;
                    const seg = sm.segment_len ?? 0;
                    const lmin = m.reflector_len_min;
                    const lmax = m.reflector_len_max;
                    const lenTxt =
                      lmin != null && lmax != null ? ` len ${seg} (${lmin}-${lmax})` : ` len ${seg}`;
                    return `${peak}/${bg}  Δ${prom}${lenTxt}`;
                  })()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
