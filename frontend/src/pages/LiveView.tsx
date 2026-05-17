import { useEffect, useState } from "react";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

type ImgMeta = {
  nw: number;
  nh: number;
  cw: number;
  ch: number;
};

export function LiveViewPage() {
  const { snapshot } = useLiveSnapshot();
  const [tick, setTick] = useState(() => Date.now());
  const [imgMeta, setImgMeta] = useState<Record<number, ImgMeta>>({});
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const cams = snapshot.cameras.length ? snapshot.cameras : [{ id: 1 }, { id: 2 }];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Canlı görünüm</h2>
      <div className="grid md:grid-cols-2 gap-4">
        {cams.map((c) => (
          <div key={c.id} className="relative rounded-lg border border-slate-700 bg-black overflow-hidden">
            <img
              alt={`cam ${c.id}`}
              className="w-full h-auto opacity-90"
              src={`/api/cameras/${c.id}/snapshot.jpg?t=${tick}`}
              onLoad={(e) => {
                const el = e.target as HTMLImageElement;
                setImgMeta((prev) => ({
                  ...prev,
                  [c.id]: {
                    nw: el.naturalWidth || 1,
                    nh: el.naturalHeight || 1,
                    cw: el.clientWidth || 1,
                    ch: el.clientHeight || 1,
                  },
                }));
              }}
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            {imgMeta[c.id] &&
              snapshot.machines
                .filter((m) => m.camera_id === c.id && m.centroid)
                .map((m) => {
                  const meta = imgMeta[c.id];
                  const cx = ((m.centroid?.x ?? 0) / meta.nw) * meta.cw;
                  const cy = ((m.centroid?.y ?? 0) / meta.nh) * meta.ch;
                  return (
                    <div
                      key={`dot-${m.id}`}
                      className="absolute"
                      style={{
                        left: `${cx}px`,
                        top: `${cy}px`,
                        width: "5px",
                        height: "5px",
                        marginLeft: "-2.5px",
                        marginTop: "-2.5px",
                        borderRadius: "9999px",
                        background: "#22c55e",
                        boxShadow: "0 0 0 1px rgba(2,6,23,0.9), 0 0 4px rgba(34,197,94,0.75)",
                        pointerEvents: "none",
                      }}
                      title={`${m.name} centroid`}
                    />
                  );
                })}
            <div className="absolute bottom-0 left-0 right-0 bg-black/60 p-2 text-xs">
              Kamera {c.id} — {(c as { status?: string }).status || "?"} — FPS ~{(c as { fps?: number }).fps?.toFixed(1) ?? "0"}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-6 grid sm:grid-cols-2 lg:grid-cols-4 gap-2 text-sm">
        {snapshot.machines.map((m) => (
          <div key={m.id} className="rounded border border-slate-700 p-2 bg-panel2">
            <strong>{m.name}</strong> {m.state} {m.mold_name ? `· ${m.mold_name}` : ""}
          </div>
        ))}
      </div>
    </div>
  );
}
