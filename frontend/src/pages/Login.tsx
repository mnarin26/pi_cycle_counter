import { useState } from "react";
import { useLocation } from "react-router-dom";
import { apiPost } from "../api/client";

export function LoginPage() {
  const location = useLocation();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const redirectTo =
    (location.state as { from?: string } | null)?.from &&
    String((location.state as { from?: string }).from).startsWith("/")
      ? String((location.state as { from?: string }).from)
      : "/";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) {
      setError("Şifre girin");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPost("/api/auth/login", { password });
      // Full reload so AuthProvider re-reads the freshly set session cookie.
      window.location.href = redirectTo;
    } catch (err) {
      setError("Şifre hatalı veya yetkiniz yok");
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm bg-panel2 border border-slate-700 rounded-lg p-6 flex flex-col gap-4"
      >
        <h1 className="text-lg font-semibold text-accent">Enjeksiyon İzleme — Giriş</h1>
        <p className="text-sm text-slate-400">
          Telegram botundan aldığınız günlük şifreyi veya yönetici şifresini girin.
        </p>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Şifre"
          className="px-3 py-2 rounded-md bg-slate-800 border border-slate-600 text-white"
        />
        {error && <div className="text-alarm text-sm">{error}</div>}
        <button
          type="submit"
          disabled={busy}
          className="px-3 py-2 rounded-md bg-accent text-slate-900 font-semibold disabled:opacity-60"
        >
          {busy ? "Giriş yapılıyor…" : "Giriş Yap"}
        </button>
      </form>
    </div>
  );
}
