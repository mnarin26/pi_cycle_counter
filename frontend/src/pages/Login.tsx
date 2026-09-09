import { useState } from "react";
import { useLocation } from "react-router-dom";
import { apiPost } from "../api/client";

export function LoginPage() {
  const location = useLocation();
  const [username, setUsername] = useState("");
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
    if (!username.trim() || !password) {
      setError("Kullanıcı adı ve şifre girin");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPost("/api/auth/login", { username: username.trim(), password });
      window.location.href = redirectTo;
    } catch (err) {
      setError("Kullanıcı adı veya şifre hatalı");
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
          Kayıtlı adınız ve şifreniz (sabit veya Telegram günlük). Yönetici: kullanıcı adı{" "}
          <code className="text-slate-300">super</code>.
        </p>
        <input
          type="text"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Kullanıcı adı"
          className="px-3 py-2 rounded-md bg-slate-800 border border-slate-600 text-white"
        />
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
