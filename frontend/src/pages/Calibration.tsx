import { useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";

type Machine = { id: number; name: string };

export function CalibrationPage() {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [mid, setMid] = useState(1);
  const [playback, setPlayback] = useState<unknown[]>([]);
  const [open1, setOpen1] = useState(0.85);
  const [closed1, setClosed1] = useState(0.15);

  useEffect(() => {
    apiGet<Machine[]>("/api/machines").then((m) => {
      setMachines(m);
      if (m.length) setMid(m[0].id);
    });
  }, []);

  async function loadPlayback() {
    const r = await apiGet<{ samples: unknown[] }>(`/api/calibration/machines/${mid}/playback`);
    setPlayback(r.samples || []);
  }

  return (
    <div className="max-w-2xl space-y-4">
      <h2 className="text-xl font-semibold">Kalibrasyon</h2>
      <label className="block text-sm">
        Makine
        <select
          className="mt-1 w-full bg-panel2 border border-slate-700 rounded p-3 min-h-[44px]"
          value={mid}
          onChange={(e) => setMid(Number(e.target.value))}
        >
          {machines.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-2 gap-4">
        <label className="text-sm">
          Açık 1D
          <input
            type="number"
            step={0.01}
            className="mt-1 w-full bg-panel2 border border-slate-700 rounded p-2"
            value={open1}
            onChange={(e) => setOpen1(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          Kapalı 1D
          <input
            type="number"
            step={0.01}
            className="mt-1 w-full bg-panel2 border border-slate-700 rounded p-2"
            value={closed1}
            onChange={(e) => setClosed1(Number(e.target.value))}
          />
        </label>
      </div>
      <button
        type="button"
        className="min-h-[44px] rounded bg-accent text-panel px-4 font-medium"
        onClick={async () => {
          await apiPost(`/api/calibration/machines/${mid}/learn`, {
            open_position_1d: open1,
            closed_position_1d: closed1,
            confidence: 0.9,
          });
        }}
      >
        Öğrenilmiş pozisyonları kaydet
      </button>
      <button type="button" className="ml-2 min-h-[44px] rounded bg-slate-700 px-4" onClick={loadPlayback}>
        Son 30 sn tamponunu yükle
      </button>
      <pre className="text-xs bg-black/40 p-3 rounded overflow-auto max-h-48">{JSON.stringify(playback.slice(-20), null, 2)}</pre>
    </div>
  );
}
