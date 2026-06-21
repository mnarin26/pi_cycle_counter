import { useEffect, useState } from "react";
import { apiGet, apiPatch, apiPost } from "../api/client";

type Camera = {
  id: number;
  name: string;
  rtsp_url: string;
  target_width: number;
  target_fps: number;
  enabled: boolean;
  status: string;
};

export function SettingsPage() {
  const [doc, setDoc] = useState<Record<string, unknown>>({});
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [savingCameraId, setSavingCameraId] = useState<number | null>(null);
  const [msg, setMsg] = useState<string>("");

  useEffect(() => {
    apiGet<{ value: Record<string, unknown> }>("/api/settings/global").then((r) => setDoc(r.value || {}));
    apiGet<Camera[]>("/api/cameras").then(setCameras);
  }, []);

  return (
    <div className="max-w-4xl space-y-6">
      <h2 className="text-xl font-semibold">Ayarlar</h2>
      {msg && <p className="text-sm text-slate-300">{msg}</p>}

      <section className="space-y-3">
        <h3 className="text-lg font-medium">RTSP Kamera Ayarları</h3>
        <div className="grid gap-3">
          {cameras.map((cam) => (
            <div key={cam.id} className="rounded border border-slate-700 bg-panel2 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">
                    {cam.name} (ID: {cam.id})
                  </div>
                  <div className="text-xs text-slate-400">Durum: {cam.status}</div>
                </div>
                <label className="text-sm flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={cam.enabled}
                    onChange={(e) =>
                      setCameras((prev) =>
                        prev.map((x) => (x.id === cam.id ? { ...x, enabled: e.target.checked } : x))
                      )
                    }
                  />
                  Aktif
                </label>
              </div>

              <label className="block text-sm">
                RTSP URL
                <input
                  className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
                  placeholder="rtsp://user:pass@ip:554/..."
                  value={cam.rtsp_url}
                  onChange={(e) =>
                    setCameras((prev) =>
                      prev.map((x) => (x.id === cam.id ? { ...x, rtsp_url: e.target.value } : x))
                    )
                  }
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  Genişlik
                  <input
                    type="number"
                    className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
                    value={cam.target_width}
                    onChange={(e) =>
                      setCameras((prev) =>
                        prev.map((x) => (x.id === cam.id ? { ...x, target_width: Number(e.target.value) } : x))
                      )
                    }
                  />
                </label>
                <label className="block text-sm">
                  Hedef FPS
                  <input
                    type="number"
                    className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
                    value={cam.target_fps}
                    onChange={(e) =>
                      setCameras((prev) =>
                        prev.map((x) => (x.id === cam.id ? { ...x, target_fps: Number(e.target.value) } : x))
                      )
                    }
                  />
                </label>
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  className="min-h-[44px] rounded bg-accent text-panel font-medium px-4"
                  disabled={savingCameraId === cam.id}
                  onClick={async () => {
                    setSavingCameraId(cam.id);
                    setMsg("");
                    try {
                      const updated = await apiPatch<Camera>(`/api/cameras/${cam.id}`, {
                        rtsp_url: cam.rtsp_url,
                        target_width: cam.target_width,
                        target_fps: cam.target_fps,
                        enabled: cam.enabled,
                      });
                      setCameras((prev) => prev.map((x) => (x.id === cam.id ? updated : x)));
                      setMsg(`${cam.name} kaydedildi.`);
                    } finally {
                      setSavingCameraId(null);
                    }
                  }}
                >
                  {savingCameraId === cam.id ? "Kaydediliyor..." : "Kamera Ayarını Kaydet"}
                </button>

                <button
                  type="button"
                  className="min-h-[44px] rounded bg-slate-700 px-4"
                  onClick={async () => {
                    const res = await apiPost<{ camera_id: number; rtsp_configured: boolean }>(
                      `/api/cameras/${cam.id}/test`
                    );
                    setMsg(
                      `${cam.name} test: ${res.rtsp_configured ? "RTSP tanımlı, worker başlatılabilir." : "RTSP boş."}`
                    );
                  }}
                >
                  Test Et
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-lg font-medium">Global JSON Ayarları</h3>
        <p className="text-sm text-slate-400">Telegram ve SSH ayarları admin panelinde (8080).</p>
        <textarea
          className="w-full h-48 bg-panel2 border border-slate-700 rounded p-3 font-mono text-sm"
          value={JSON.stringify(doc, null, 2)}
          onChange={(e) => {
            try {
              setDoc(JSON.parse(e.target.value));
            } catch {
              /* keep */
            }
          }}
        />
        <button
          type="button"
          className="min-h-[44px] rounded bg-accent text-panel font-medium px-4"
          onClick={async () => {
            await apiPatch("/api/settings/global", doc);
            setMsg("Global ayarlar kaydedildi.");
          }}
        >
          Global Ayarları Kaydet
        </button>
      </section>
    </div>
  );
}
