import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";
import { useAuth } from "../hooks/useAuth";
import { apiPost } from "../api/client";

const NAV_PIN_KEY = "operatorNavPinned";

const nav = [
  ["/", "Pano", "P"],
  ["/tv", "Bilgi Ekranı", "TV"],
  ["/molds", "Kalıplar", "K"],
  ["/planning", "Planlama", "Pl"],
  ["/events", "Olaylar", "O"],
] as const;

export function Layout() {
  const { connected, snapshot } = useLiveSnapshot();
  const { user, logout } = useAuth();
  const cameras = snapshot.cameras ?? [];
  const fps = cameras.map((c) => c.fps || 0).reduce((a, b) => a + b, 0);
  const cpu = Number(snapshot.cpu_proxy ?? 0);
  const wsClients = Number(snapshot.ws_clients ?? 0);
  const [pwOpen, setPwOpen] = useState(false);
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwAgain, setPwAgain] = useState("");
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwBusy, setPwBusy] = useState(false);
  const [pinned, setPinned] = useState(() => {
    try {
      return localStorage.getItem(NAV_PIN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const canChangePassword = Boolean(user) && !user?.is_super;

  useEffect(() => {
    try {
      localStorage.setItem(NAV_PIN_KEY, pinned ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [pinned]);

  async function savePassword() {
    setPwMsg(null);
    if (!pwCurrent || !pwNew) {
      setPwMsg("Mevcut ve yeni şifreyi yazın");
      return;
    }
    if (pwNew !== pwAgain) {
      setPwMsg("Yeni şifreler eşleşmiyor");
      return;
    }
    setPwBusy(true);
    try {
      await apiPost("/api/auth/change-password", {
        current_password: pwCurrent,
        new_password: pwNew,
      });
      setPwCurrent("");
      setPwNew("");
      setPwAgain("");
      setPwMsg("Şifre güncellendi");
    } catch (e) {
      setPwMsg(String(e));
    } finally {
      setPwBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-700 bg-panel2 px-4 py-3 flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-semibold tracking-tight text-accent">Enjeksiyon İzleme</h1>
        <div className="ml-auto flex items-center gap-4 text-xs text-slate-400">
          <span>
            WS: {connected ? <span className="text-ok">bağlı</span> : <span className="text-alarm">kopuk</span>}
          </span>
          <span>Ekran~ {wsClients > 0 ? wsClients : "—"}</span>
          <span>FPS~ {fps > 0 ? fps.toFixed(1) : "—"}</span>
          <span>İşlem ms~ {cpu > 0 ? cpu.toFixed(1) : "—"}</span>
          {user && (
            <span className="flex items-center gap-2">
              <span className="text-slate-300">{user.display_name}</span>
              {canChangePassword && (
                <button
                  type="button"
                  onClick={() => {
                    setPwOpen(true);
                    setPwMsg(null);
                  }}
                  className="px-2 py-1 rounded-md bg-slate-700 text-slate-200 hover:bg-slate-600"
                >
                  Şifre değiştir
                </button>
              )}
              <button
                onClick={logout}
                className="px-2 py-1 rounded-md bg-slate-700 text-slate-200 hover:bg-slate-600"
              >
                Çıkış
              </button>
            </span>
          )}
        </div>
      </header>
      <div className="flex flex-1 min-h-0 relative">
        <div
          className={`op-nav-rail relative z-30 shrink-0 ${pinned ? "is-pinned w-60" : "w-[52px]"}`}
        >
          <nav
            className="op-nav absolute left-0 top-0 h-full min-h-full overflow-x-hidden overflow-y-auto border-r border-slate-700 bg-panel2 px-1.5 py-2.5 box-border"
            aria-label="Sayfalar"
          >
            <div className="mb-2.5 flex min-h-9 items-center px-0.5">
              <button
                type="button"
                className={`inline-flex h-9 min-w-9 items-center justify-center gap-2 rounded-lg border px-2 text-xs font-semibold whitespace-nowrap ${
                  pinned
                    ? "border-sky-600 bg-sky-950 text-sky-300"
                    : "border-slate-600 bg-slate-800 text-slate-300 hover:bg-slate-700"
                }`}
                aria-pressed={pinned}
                title={pinned ? "Sabitlemeyi kaldır" : "Menüyü sabitle"}
                onClick={() => setPinned((v) => !v)}
              >
                <span aria-hidden="true">{pinned ? "◉" : "⊙"}</span>
                <span className="op-nav-label">{pinned ? "Sabitli" : "Sabitle"}</span>
              </button>
            </div>
            {nav.map(([to, label, emblem]) => (
              <NavLink
                key={to}
                to={to}
                title={label}
                end={to === "/"}
                className={({ isActive }) =>
                  `op-nav-item mb-1.5 flex min-h-10 w-full items-center gap-2.5 rounded-lg border px-2 py-2 text-sm ${
                    isActive
                      ? "border-sky-600 bg-sky-950 font-semibold text-sky-300 shadow-[inset_3px_0_0_#38bdf8]"
                      : "border-transparent text-slate-300 hover:bg-slate-800"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <span
                      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[11px] font-bold tracking-wide ${
                        isActive ? "bg-blue-700 text-sky-50" : "bg-slate-800 text-slate-400"
                      }`}
                      aria-hidden="true"
                    >
                      {emblem}
                    </span>
                    <span className="op-nav-label whitespace-nowrap overflow-hidden">{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>
        <main className="flex-1 min-w-0 p-4 overflow-auto">
          <Outlet />
        </main>
      </div>
      {pwOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-lg border border-slate-600 bg-panel2 p-4">
            <h3 className="mb-3 text-lg font-semibold">Şifre değiştir</h3>
            <div className="space-y-2">
              <input
                type="password"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="Mevcut şifre (sabit veya Telegram)"
                value={pwCurrent}
                onChange={(e) => setPwCurrent(e.target.value)}
              />
              <input
                type="password"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="Yeni sabit şifre"
                value={pwNew}
                onChange={(e) => setPwNew(e.target.value)}
              />
              <input
                type="password"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="Yeni şifre tekrar"
                value={pwAgain}
                onChange={(e) => setPwAgain(e.target.value)}
              />
            </div>
            {pwMsg && <p className="mt-2 text-xs text-slate-300">{pwMsg}</p>}
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="rounded bg-accent px-3 py-2 text-sm text-panel disabled:opacity-50"
                disabled={pwBusy}
                onClick={() => void savePassword()}
              >
                {pwBusy ? "Kaydediliyor…" : "Kaydet"}
              </button>
              <button
                type="button"
                className="rounded bg-slate-700 px-3 py-2 text-sm"
                onClick={() => setPwOpen(false)}
              >
                Kapat
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
