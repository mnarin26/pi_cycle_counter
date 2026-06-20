import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";

export type MachineSnap = {
  id: number;
  name: string;
  camera_id: number;
  state: string;
  position_01: number | null;
  centroid: { x: number; y: number } | null;
  roi_bbox: number[] | null;
  cycle_time_last: number | null;
  mold_name: string | null;
  confidence: number;
  fps: number;
  process_ms: number;
  threshold_mode?: string;
  threshold_active_min?: number;
  threshold_active_max?: number;
  threshold_offset?: number;
  peak?: number;
  background?: number;
  prominence?: number;
  segment_len?: number;
  line_thickness?: number;
  reflector_len_min?: number | null;
  reflector_len_max?: number | null;
  dbg_cycle_emit_count?: number;
};

export type Snapshot = {
  machines: MachineSnap[];
  cameras: { id: number; status: string; fps: number }[];
  cpu_proxy: number;
};

const defaultSnap: Snapshot = { machines: [], cameras: [], cpu_proxy: 0 };

const Ctx = createContext<{ snapshot: Snapshot; connected: boolean }>({
  snapshot: defaultSnap,
  connected: false,
});

export function useLiveSnapshot() {
  return useContext(Ctx);
}

function wsUrl() {
  const p = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${p}//${window.location.host}/ws`;
}

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot>(defaultSnap);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let stopped = false;
    function connect() {
      if (stopped) return;
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) setTimeout(connect, 2000);
      };
      ws.onerror = () => setConnected(false);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "snapshot" && msg.data) setSnapshot(msg.data as Snapshot);
        } catch {
          /* ignore */
        }
      };
    }
    connect();
    return () => {
      stopped = true;
      wsRef.current?.close();
    };
  }, []);

  const value = useMemo(() => ({ snapshot, connected }), [snapshot, connected]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
