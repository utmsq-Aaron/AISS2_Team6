import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import { CoachPoll } from "./components/CoachPoll";
import { RouteErrorBoundary } from "./components/ErrorBoundary";
import { Header } from "./components/Header";
import { OnboardingWizard } from "./components/onboarding/OnboardingWizard";
import { Sidebar } from "./components/Sidebar";
import { Spinner } from "./components/Spinner";
import { getProfile } from "./lib/api";
import { Chat } from "./pages/Chat";
import { Coach } from "./pages/Coach";
import { Dashboard } from "./pages/Dashboard";
import { Health } from "./pages/Health";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { useAuthStore } from "./store/authStore";

export default function App() {
  // Gate on the persisted user hint (the session lives in an httpOnly cookie the
  // JS can't see). If the cookie is actually stale, the first authenticated call
  // 401s and forceLogout() clears this and drops back here.
  const user = useAuthStore((s) => s.user);
  if (!user) return <Login />;

  return <Authenticated />;
}

// Split out so the profile query (which requires a session) only mounts once
// logged in — hooks can't be called conditionally in the same component.
function Authenticated() {
  const profileQuery = useQuery({ queryKey: ["profile"], queryFn: getProfile });

  if (profileQuery.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg-app">
        <Spinner label="Loading your profile…" />
      </div>
    );
  }

  // Only gate on an explicit `false` from a successfully-loaded profile — never
  // lock a user out of the app because the profile endpoint hiccuped once.
  if (profileQuery.data && !profileQuery.data.onboarding_complete) {
    return <OnboardingWizard onDone={() => profileQuery.refetch()} />;
  }

  return <MainShell />;
}

function MainShell() {
  return (
    <div className="flex h-screen overflow-hidden bg-bg-app text-text-primary">
      <CoachPoll />
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5">
          <RouteErrorBoundary>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/coach" element={<Coach />} />
              <Route path="/health" element={<Health />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
  );
}
