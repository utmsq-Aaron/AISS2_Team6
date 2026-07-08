import { Navigate, Route, Routes } from "react-router-dom";

import { Header } from "./components/Header";
import { Sidebar } from "./components/Sidebar";
import { Analysis } from "./pages/Analysis";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Health } from "./pages/Health";
import { RoutesPage } from "./pages/RoutesPage";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Sync } from "./pages/Sync";
import { useAuthStore } from "./store/authStore";

export default function App() {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Login />;

  return (
    <div className="flex h-screen overflow-hidden bg-bg-app text-text-primary">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5">
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
