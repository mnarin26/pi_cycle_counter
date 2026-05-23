import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { LiveProvider } from "./hooks/useLiveSnapshot";
import { AnalyticsPage } from "./pages/Analytics";
import { CalibrationPage } from "./pages/Calibration";
import { DashboardPage } from "./pages/Dashboard";
import { EventsPage } from "./pages/Events";
import { LiveViewPage } from "./pages/LiveView";
import { MachineDetailPage } from "./pages/MachineDetail";
import { MachinesPage } from "./pages/Machines";
import { MoldsPage } from "./pages/Molds";

export default function App() {
  return (
    <BrowserRouter>
      <LiveProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="live" element={<LiveViewPage />} />
            <Route path="machines" element={<MachinesPage />} />
            <Route path="machines/:id" element={<MachineDetailPage />} />
            <Route path="molds" element={<MoldsPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="events" element={<EventsPage />} />
            <Route path="calibration" element={<CalibrationPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </LiveProvider>
    </BrowserRouter>
  );
}
