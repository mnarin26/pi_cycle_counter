import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { apiGet, apiPost } from "../api/client";

type Me = {
  actor_type: string;
  display_name: string;
  permissions: Record<string, boolean>;
  is_super: boolean;
};

type AuthState = {
  loading: boolean;
  user: Me | null;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({ loading: true, user: null, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<Me | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (active) setUser(data);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function logout() {
    try {
      await apiPost("/api/auth/logout");
    } catch {
      /* ignore */
    }
    setUser(null);
    location.href = "/login";
  }

  return <AuthContext.Provider value={{ loading, user, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { loading, user } = useAuth();
  const location = useLocation();
  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">Yükleniyor…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
