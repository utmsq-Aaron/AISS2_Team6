import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { Sidebar } from "./components/Sidebar";
import { Analysis } from "./pages/Analysis";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Health } from "./pages/Health";
import { RoutesPage } from "./pages/RoutesPage";
import { Settings } from "./pages/Settings";
import { Sync } from "./pages/Sync";

const TABS = [
  { to: "/dashboard", label: "📊  Dashboard" },
  { to: "/health", label: "🏥  Health" },
  { to: "/routes", label: "🗺️  Routes" },
  { to: "/analysis", label: "📈  Analysis" },
  { to: "/chat", label: "💬  Chat" },
  { to: "/sync", label: "🔁  Sync" },
  { to: "/settings", label: "⚙️  Settings" },
];

export default function App() {
  return (
    <div className="flex h-screen overflow-hidden bg-bg-app text-text-primary">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <nav className="flex flex-shrink-0 gap-1 border-b border-border px-6 pt-3">
          {TABS.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) => `fd-tab ${isActive ? "fd-tab-active" : ""}`}
            >
              {t.label}
            </NavLink>
          ))}
        </nav>
        <main className="flex-1 overflow-y-auto px-6 py-5">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/health" element={<Health />} />
            <Route path="/routes" element={<RoutesPage />} />
            <Route path="/analysis" element={<Analysis />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/sync" element={<Sync />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
