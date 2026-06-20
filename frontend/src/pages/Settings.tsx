import { useCallback, useEffect, useState } from "react";
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

type TelegramSettings = {
  enabled: boolean;
  bot_username: string;
  allowed_user_ids: string;
  token_set: boolean;
  token_hint: string | null;
};

type SshSettings = {
  host: string;
  user: string;
  port: number;
  auth_method: "key" | "password";
  key_path: string;
  alias: string;
  connection_string: string;
};

const EMPTY_TELEGRAM: TelegramSettings = {
  enabled: false,
  bot_username: "Alfamold_bot",
  allowed_user_ids: "",
  token_set: false,
  token_hint: null,
};

const EMPTY_SSH: SshSettings = {
  host: "100.92.41.26",
  user: "pi",
  port: 22,
  auth_method: "key",
  key_path: "~/.ssh/id_ed25519",
  alias: "rsp3b",
  connection_string: "ssh pi@100.92.41.26",
};

export function SettingsPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [telegram, setTelegram] = useState<TelegramSettings>(EMPTY_TELEGRAM);
  const [telegramToken, setTelegramToken] = useState("");
  const [ssh, setSsh] = useState<SshSettings>(EMPTY_SSH);
  const [savingCameraId, setSavingCameraId] = useState<number | null>(null);
  const [savingTelegram, setSavingTelegram] = useState(false);
  const [savingSsh, setSavingSsh] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const loadTelegram = useCallback(async () => {
    const r = await apiGet<TelegramSettings>("/api/settings/telegram");
    setTelegram({ ...EMPTY_TELEGRAM, ...r });
    setTelegramToken("");
  }, []);

  const loadSsh = useCallback(async () => {
    const r = await apiGet<SshSettings>("/api/settings/ssh");
    setSsh({ ...EMPTY_SSH, ...r });
  }, []);

  useEffect(() => {
    apiGet<Camera[]>("/api/cameras").then(setCameras);
    loadTelegram().catch(() => setMsg("Telegram ayarları yüklenemedi."));
    loadSsh().catch(() => setMsg("SSH ayarları yüklenemedi."));
  }, [loadTelegram, loadSsh]);

  return (
    <div className="max-w-4xl space-y-8">
      <h2 className="text-xl font-semibold">Ayarlar</h2>
      {msg && <p className="text-sm text-slate-300">{msg}</p>}

      <section className="rounded border border-slate-700 bg-panel2 p-4 space-y-4">
        <div>
          <h3 className="text-lg font-medium">Telegram Kalıp Botu</h3>
          <p className="text-sm text-slate-400 mt-1">
            @BotFather tokenını buraya kaydedin. Token kaydedildikten sonra arayüzde sadece son 4 hane görünür.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={telegram.enabled}
            onChange={(e) => setTelegram((t) => ({ ...t, enabled: e.target.checked }))}
          />
          Bot etkin (servis kurulunca kullanılır)
        </label>

        <label className="block text-sm">
          Bot kullanıcı adı (referans)
          <input
            className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
            placeholder="Alfamold_bot"
            value={telegram.bot_username}
            onChange={(e) => setTelegram((t) => ({ ...t, bot_username: e.target.value }))}
          />
        </label>

        <label className="block text-sm">
          Bot token
          <input
            type="password"
            autoComplete="off"
            className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-sm"
            placeholder={
              telegram.token_set
                ? `Kayıtlı (…${telegram.token_hint ?? "****"}) — değiştirmek için yeni token yazın`
                : "BotFather tokenını buraya yapıştırın"
            }
            value={telegramToken}
            onChange={(e) => setTelegramToken(e.target.value)}
          />
        </label>

        <label className="block text-sm">
          İzinli Telegram user ID (virgülle)
          <input
            className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-sm"
            placeholder="123456789,987654321"
            value={telegram.allowed_user_ids}
            onChange={(e) => setTelegram((t) => ({ ...t, allowed_user_ids: e.target.value }))}
          />
          <span className="text-xs text-slate-500">
            Operatör bota /start yazdıktan sonra @userinfobot ile ID öğrenilebilir.
          </span>
        </label>

        <button
          type="button"
          className="min-h-[44px] rounded bg-accent text-panel font-medium px-4"
          disabled={savingTelegram}
          onClick={async () => {
            setSavingTelegram(true);
            setMsg("");
            try {
              const body: Record<string, unknown> = {
                enabled: telegram.enabled,
                bot_username: telegram.bot_username.trim(),
                allowed_user_ids: telegram.allowed_user_ids.trim(),
              };
              if (telegramToken.trim()) {
                body.bot_token = telegramToken.trim();
              }
              const updated = await apiPatch<TelegramSettings>("/api/settings/telegram", body);
              setTelegram({ ...EMPTY_TELEGRAM, ...updated });
              setTelegramToken("");
              setMsg("Telegram ayarları kaydedildi.");
            } catch (e) {
              setMsg(e instanceof Error ? e.message : "Telegram kaydı başarısız.");
            } finally {
              setSavingTelegram(false);
            }
          }}
        >
          {savingTelegram ? "Kaydediliyor..." : "Telegram Ayarlarını Kaydet"}
        </button>
      </section>

      <section className="rounded border border-slate-700 bg-panel2 p-4 space-y-4">
        <div>
          <h3 className="text-lg font-medium">Pi SSH Bağlantısı</h3>
          <p className="text-sm text-slate-400 mt-1">
            Uzak erişim bilgisi (Tailscale IP, kullanıcı). Şifre burada saklanmaz; SSH anahtarı önerilir.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block text-sm">
            Host / IP
            <input
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono"
              value={ssh.host}
              onChange={(e) => setSsh((s) => ({ ...s, host: e.target.value }))}
            />
          </label>
          <label className="block text-sm">
            Kullanıcı
            <input
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
              value={ssh.user}
              onChange={(e) => setSsh((s) => ({ ...s, user: e.target.value }))}
            />
          </label>
          <label className="block text-sm">
            Port
            <input
              type="number"
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
              value={ssh.port}
              onChange={(e) => setSsh((s) => ({ ...s, port: Number(e.target.value) || 22 }))}
            />
          </label>
          <label className="block text-sm">
            SSH alias (opsiyonel)
            <input
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
              placeholder="rsp3b"
              value={ssh.alias}
              onChange={(e) => setSsh((s) => ({ ...s, alias: e.target.value }))}
            />
          </label>
        </div>

        <label className="block text-sm">
          Kimlik doğrulama
          <select
            className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2"
            value={ssh.auth_method}
            onChange={(e) =>
              setSsh((s) => ({ ...s, auth_method: e.target.value as "key" | "password" }))
            }
          >
            <option value="key">SSH anahtarı (önerilen)</option>
            <option value="password">Şifre (sadece yerel ~/.ssh/config)</option>
          </select>
        </label>

        {ssh.auth_method === "key" && (
          <label className="block text-sm">
            Anahtar yolu
            <input
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-sm"
              value={ssh.key_path}
              onChange={(e) => setSsh((s) => ({ ...s, key_path: e.target.value }))}
            />
          </label>
        )}

        {ssh.connection_string && (
          <div className="text-sm text-slate-300">
            Bağlantı:{" "}
            <code className="bg-slate-900 px-2 py-1 rounded">{ssh.connection_string}</code>
            <button
              type="button"
              className="ml-2 text-accent underline"
              onClick={() => {
                void navigator.clipboard.writeText(ssh.connection_string);
                setMsg("SSH komutu panoya kopyalandı.");
              }}
            >
              Kopyala
            </button>
          </div>
        )}

        <button
          type="button"
          className="min-h-[44px] rounded bg-accent text-panel font-medium px-4"
          disabled={savingSsh}
          onClick={async () => {
            setSavingSsh(true);
            setMsg("");
            try {
              const updated = await apiPatch<SshSettings>("/api/settings/ssh", {
                host: ssh.host.trim(),
                user: ssh.user.trim(),
                port: ssh.port,
                auth_method: ssh.auth_method,
                key_path: ssh.key_path.trim(),
                alias: ssh.alias.trim(),
              });
              setSsh({ ...EMPTY_SSH, ...updated });
              setMsg("SSH ayarları kaydedildi.");
            } catch (e) {
              setMsg(e instanceof Error ? e.message : "SSH kaydı başarısız.");
            } finally {
              setSavingSsh(false);
            }
          }}
        >
          {savingSsh ? "Kaydediliyor..." : "SSH Ayarlarını Kaydet"}
        </button>
      </section>

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

              <div className="flex gap-2 flex-wrap">
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
    </div>
  );
}
