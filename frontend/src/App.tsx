import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { LiveProvider } from "./hooks/useLiveSnapshot";
import { AuthProvider, RequireAuth } from "./hooks/useAuth";
import { AnalyticsPage } from "./pages/Analytics";
import { DashboardPage } from "./pages/Dashboard";
import { EventsPage } from "./pages/Events";
import { LoginPage } from "./pages/Login";
import { MachineDetailPage } from "./pages/MachineDetail";
import { MoldsPage } from "./pages/Molds";
import { TvWallPage } from "./pages/TvWall";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LiveProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/tv" element={<TvWallPage />} />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route index element={<DashboardPage />} />
              <Route path="machines/:id" element={<MachineDetailPage />} />
              <Route path="molds" element={<MoldsPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </LiveProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
