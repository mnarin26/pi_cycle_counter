import { NavLink, Outlet } from "react-router-dom";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

const nav = [
  ["/", "Pano"],
  ["/live", "Canlı"],
  ["/machines", "Makineler"],
  ["/molds", "Kalıplar"],
  ["/analytics", "Analitik"],
  ["/events", "Olaylar"],
  ["/settings", "Ayarlar"],
  ["/calibration", "Kalibrasyon"],
];

export function Layout() {
  const { connected, snapshot } = useLiveSnapshot();
  const fps = snapshot.cameras.map((c) => c.fps).reduce((a, b) => a + b, 0);

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-700 bg-panel2 px-4 py-3 flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-semibold tracking-tight text-accent">Enjeksiyon İzleme</h1>
        <nav className="flex flex-wrap gap-2">
          {nav.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `px-3 py-2 rounded-md text-sm min-h-[44px] flex items-center ${
                  isActive ? "bg-slate-700 text-white" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex gap-4 text-xs text-slate-400">
          <span>WS: {connected ? <span className="text-ok">bağlı</span> : <span className="text-alarm">kopuk</span>}</span>
          <span>FPS~ {fps.toFixed(1)}</span>
          <span>İşlem ms~ {snapshot.cpu_proxy.toFixed(1)}</span>
        </div>
      </header>
      <main className="flex-1 p-4">
        <Outlet />
      </main>
    </div>
  );
}
