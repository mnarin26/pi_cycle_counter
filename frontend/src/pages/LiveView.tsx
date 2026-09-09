import { useEffect, useRef, useState } from "react";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

type ImgMeta = {
  nw: number;
  nh: number;
  cw: number;
  ch: number;
};

function liveDotColor(state?: string | null): string {
  switch (state) {
    case "OPEN":
      return "#22d3ee";
    case "CLOSED":
      return "#3b82f6";
    case "MOVING":
      return "#fbbf24";
    default:
      return "#94a3b8";
  }
}

// #region agent log
function dbgLive(hypothesisId: string, location: string, message: string, data: Record<string, unknown>) {
  const body = {
    sessionId: "3a2fad",
    runId: "m3-live",
    hypothesisId,
    location,
    message,
    data,
    timestamp: Date.now(),
  };
  fetch("http://127.0.0.1:7662/ingest/cbe83440-6fdc-44a5-a3d6-275b7d410b22", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "3a2fad" },
    body: JSON.stringify(body),
  }).catch(() => {});
  // Fallback: Pi backend (works when local ingest is unreachable)
  fetch("/api/debug/agent-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {});
}
// #endregion

export function LiveViewPage() {
  const { snapshot } = useLiveSnapshot();
  const [tick, setTick] = useState(() => Date.now());
  const [imgMeta, setImgMeta] = useState<Record<number, ImgMeta>>({});
  const [selectedCamId, setSelectedCamId] = useState<number>(1);
  const lastPosRef = useRef<number | null>(null);
  const lastCentroidRef = useRef<{ x: number; y: number } | null>(null);
  const frameSigRef = useRef<string>("");
  const sampleNRef = useRef(0);
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const cams = snapshot.cameras.length ? snapshot.cameras : [{ id: 1 }, { id: 2 }];
  const activeMachines = snapshot.machines.filter((m) => m.state !== "DISABLED");
  useEffect(() => {
    if (!cams.length) return;
    const exists = cams.some((c) => c.id === selectedCamId);
    if (!exists) {
      setSelectedCamId(cams[0].id);
    }
  }, [selectedCamId, cams]);
  const selectedCam = cams.find((c) => c.id === selectedCamId) ?? cams[0];

  // #region agent log
  useEffect(() => {
    const m3 = snapshot.machines.find((m) => m.id === 3);
    if (!m3) {
      dbgLive("D", "LiveView.tsx:m3-missing", "machine 3 not in snapshot", {
        machineCount: snapshot.machines.length,
        selectedCamId,
      });
      return;
    }
    const pos = m3.position_01;
    const c = m3.centroid;
    const prevPos = lastPosRef.current;
    const prevC = lastCentroidRef.current;
    const dPos = prevPos != null && pos != null ? Math.abs(pos - prevPos) : null;
    const dCent = prevC && c ? Math.hypot(c.x - prevC.x, c.y - prevC.y) : null;
    lastPosRef.current = pos ?? null;
    lastCentroidRef.current = c ? { x: c.x, y: c.y } : null;
    sampleNRef.current += 1;
    if (sampleNRef.current % 4 === 1) {
      const meta = imgMeta[selectedCam?.id ?? -1];
      const onSelectedCam = m3.camera_id === selectedCam?.id;
      let cxPct: number | null = null;
      let cyPct: number | null = null;
      let offscreen = false;
      if (meta && c) {
        cxPct = (c.x / meta.nw) * 100;
        cyPct = (c.y / meta.nh) * 100;
        offscreen = c.x < 0 || c.y < 0 || c.x > meta.nw || c.y > meta.nh;
      }
      dbgLive("B", "LiveView.tsx:m3-motion", "m3 live motion sample", {
        state: m3.state,
        pos,
        dPos,
        dCent,
        idle_s: m3.idle_s ?? null,
        occlusion_hold: (m3 as { occlusion_hold?: boolean }).occlusion_hold ?? null,
        centroid: c,
        onSelectedCam,
        selectedCamId: selectedCam?.id ?? null,
        camera_id: m3.camera_id,
        imgNw: meta?.nw ?? null,
        imgNh: meta?.nh ?? null,
        cxPct,
        cyPct,
        offscreen,
        cycle_time_last: m3.cycle_time_last,
        dbg_cycle_emit_count: m3.dbg_cycle_emit_count ?? null,
      });
    }
  }, [snapshot, selectedCam?.id, imgMeta]);

  useEffect(() => {
    if (!selectedCam) return;
    const url = `/api/cameras/${selectedCam.id}/snapshot.jpg?t=${tick}`;
    let cancelled = false;
    fetch(url, { cache: "no-store" })
      .then(async (r) => {
        if (cancelled) return;
        const age = r.headers.get("X-Frame-Age-Ms");
        const buf = await r.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let sum = 0;
        const step = Math.max(1, Math.floor(bytes.length / 64));
        for (let i = 0; i < bytes.length; i += step) sum = (sum + bytes[i]) % 9973;
        const sig = `${bytes.length}:${sum}`;
        const sameAsPrev = sig === frameSigRef.current;
        frameSigRef.current = sig;
        if (sampleNRef.current % 4 === 1) {
          dbgLive("A", "LiveView.tsx:frame", "snapshot frame sample", {
            camId: selectedCam.id,
            http: r.status,
            ageMs: age != null ? Number(age) : null,
            bytes: bytes.length,
            sameAsPrev,
            sig,
          });
        }
      })
      .catch((err) => {
        dbgLive("E", "LiveView.tsx:frame-err", "snapshot fetch failed", {
          camId: selectedCam.id,
          err: String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [tick, selectedCam?.id]);
  // #endregion

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Canlı görünüm</h2>
      <div className="mb-4 max-w-xs">
        <label className="mb-1 block text-sm text-slate-300">Kamera</label>
        <select
          className="w-full rounded border border-slate-700 bg-panel2 p-2"
          value={selectedCam?.id ?? 1}
          onChange={(e) => setSelectedCamId(Number(e.target.value))}
        >
          {cams.map((c) => (
            <option key={c.id} value={c.id}>
              Kamera {c.id}
            </option>
          ))}
        </select>
      </div>
      <div className="grid md:grid-cols-1 gap-4">
        {selectedCam && (
          <div key={selectedCam.id} className="relative rounded-lg border border-slate-700 bg-black overflow-hidden">
            <img
              alt={`cam ${selectedCam.id}`}
              className="w-full h-auto opacity-90"
              src={`/api/cameras/${selectedCam.id}/snapshot.jpg?t=${tick}`}
              onLoad={(e) => {
                const el = e.target as HTMLImageElement;
                setImgMeta((prev) => ({
                  ...prev,
                  [selectedCam.id]: {
                    nw: el.naturalWidth || 1,
                    nh: el.naturalHeight || 1,
                    cw: el.clientWidth || 1,
                    ch: el.clientHeight || 1,
                  },
                }));
              }}
              onError={(e) => {
                // #region agent log
                dbgLive("E", "LiveView.tsx:img-onError", "img onError hid snapshot", {
                  camId: selectedCam.id,
                });
                // #endregion
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            {imgMeta[selectedCam.id] &&
              activeMachines
                .filter((m) => m.camera_id === selectedCam.id && m.centroid)
                .map((m) => {
                  const meta = imgMeta[selectedCam.id];
                  const cx = ((m.centroid?.x ?? 0) / meta.nw) * meta.cw;
                  const cy = ((m.centroid?.y ?? 0) / meta.nh) * meta.ch;
                  const color = liveDotColor(m.state);
                  return (
                    <div
                      key={`dot-${m.id}`}
                      className="absolute"
                      style={{
                        left: `${cx}px`,
                        top: `${cy}px`,
                        width: "7px",
                        height: "7px",
                        marginLeft: "-3.5px",
                        marginTop: "-3.5px",
                        borderRadius: "9999px",
                        background: color,
                        boxShadow: `0 0 0 1px rgba(2,6,23,0.9), 0 0 6px ${color}`,
                        pointerEvents: "none",
                      }}
                      title={`${m.name} ${m.state} pos=${m.position_01 != null ? m.position_01.toFixed(3) : "—"}`}
                    />
                  );
                })}
            <div className="absolute bottom-0 left-0 right-0 bg-black/60 p-2 text-xs">
              Kamera {selectedCam.id} — {(selectedCam as { status?: string }).status || "?"} — FPS ~
              {(selectedCam as { fps?: number }).fps?.toFixed(1) ?? "0"}
            </div>
          </div>
        )}
      </div>
      <div className="mt-6 grid sm:grid-cols-2 lg:grid-cols-4 gap-2 text-sm">
        {activeMachines
          .filter((m) => m.camera_id === (selectedCam?.id ?? -1))
          .map((m) => (
            <div key={m.id} className="rounded border border-slate-700 p-2 bg-panel2">
              <strong>{m.name}</strong> {m.state}
              {m.mold_name ? ` · ${m.mold_name}` : ""}
              {m.position_01 != null ? ` · pos=${m.position_01.toFixed(3)}` : ""}
            </div>
          ))}
      </div>
    </div>
  );
}
