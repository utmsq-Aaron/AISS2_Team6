// Dashboard — a minimal, multi-goal home. The dense analytics that used to live
// here (period selector, activity search, 5-col metric grid, activity map + delete
// + flythrough, recent activities, training charts, official Strava stats) now
// live on the Analysis page. This page is: a slim athlete greeting, a responsive
// grid of agent-authored goal panels, a quick weather glance, and a coach
// placeholder.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, MessageSquare, Target } from "lucide-react";

import { AddGoalInput } from "../components/goal/AddGoalInput";
import { GoalPanel } from "../components/goal/GoalPanel";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { Spinner, ErrorBox } from "../components/Spinner";
import { callTool } from "../lib/api";
import type { Goal } from "../lib/api";
import {
  useAddGoal,
  useDeleteGoal,
  useGoals,
  useRefreshGoalPanel,
  useUpdateGoal,
} from "../lib/goalQueries";
import { useAvatarUrl, useProfile } from "../lib/profileHooks";
import { useUiStore } from "../store/uiStore";
import type { AthleteResult, AthleteProfile } from "../lib/stravaTypes";

// Karlsruhe — used when the athlete profile gives no coords.
const KARLSRUHE = { lat: 49.0069, lon: 8.4037 };

interface CurrentWeather {
  location: string;
  temperature_c: number;
  wind_speed_kmh: number;
  weather_code: number;
  weather_condition: string;
}

// WMO condition lookup (subset from ui/dashboard.py).
const WMO: Record<number, string> = {
  0: "☀️ Clear", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
  45: "🌫️ Foggy", 48: "🌫️ Foggy",
  51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌧️ Dense drizzle",
  61: "🌧️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain",
  71: "🌨️ Light snow", 73: "🌨️ Snow", 75: "❄️ Heavy snow",
  80: "🌦️ Rain showers", 81: "🌧️ Rain showers", 82: "⛈️ Violent showers",
  95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm", 99: "⛈️ Thunderstorm",
};

// Sort: building goals first (freshly added, visible at a glance), then newest.
function sortGoals(goals: Goal[]): Goal[] {
  return [...goals].sort((a, b) => {
    const aBuilding = a.panel_status === "building" ? 0 : 1;
    const bBuilding = b.panel_status === "building" ? 0 : 1;
    if (aBuilding !== bBuilding) return aBuilding - bBuilding;
    return (b.created_at || "").localeCompare(a.created_at || "");
  });
}

export function Dashboard() {
  const refreshVersion = useUiStore((s) => s.refreshVersion);

  // ── Goals ──
  const goalsQ = useGoals();
  const addGoal = useAddGoal();
  const updateGoal = useUpdateGoal();
  const deleteGoal = useDeleteGoal();
  const refreshPanel = useRefreshGoalPanel();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  const activeGoals = sortGoals((goalsQ.data ?? []).filter((g) => g.status === "active"));

  // ── Athlete (name + avatar only) ──
  const athleteQ = useQuery({
    queryKey: ["athlete", refreshVersion],
    queryFn: () => callTool<AthleteResult>("strava__get_athlete_profile", {}),
  });
  const stravaProfile = athleteQ.data?.profile ?? {};
  const coords = {
    lat: stravaProfile.lat ?? KARLSRUHE.lat,
    lon: stravaProfile.lon ?? KARLSRUHE.lon,
  };

  // ── Onboarding profile (name + avatar the user actually set) — takes
  // priority over the deployment-global Strava identity in the greeting. ──
  const profileQuery = useProfile();
  const avatarUrl = useAvatarUrl(Boolean(profileQuery.data?.has_avatar));

  // ── Weather (one compact current-conditions card) ──
  const weatherQ = useQuery({
    queryKey: ["dash-weather", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<CurrentWeather>("weather__get_current_weather", coords),
  });

  function startEdit(goal: Goal) {
    setEditingId(goal.id);
    setEditText(goal.text);
  }

  function commitEdit(goal: Goal) {
    const text = editText.trim();
    setEditingId(null);
    if (text && text !== goal.text) {
      updateGoal.mutate({ id: goal.id, patch: { text } });
    }
  }

  return (
    <div>
      <AthleteGreeting
        profile={stravaProfile}
        profileName={profileQuery.data?.name}
        avatarUrl={avatarUrl}
        loading={athleteQ.isLoading}
      />

      {/* ── Quick glance ── */}
      <section className="mt-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard
            label={`Weather ${weatherQ.data?.location ?? ""}`.trim()}
            value={
              weatherQ.isLoading
                ? "…"
                : weatherQ.data
                  ? WMO[weatherQ.data.weather_code] ?? "🌡️"
                  : "—"
            }
            sub={weatherQ.data ? `${weatherQ.data.temperature_c} °C` : "unavailable"}
          />
          <MetricCard
            label="Active goals"
            value={goalsQ.isLoading ? "…" : String(activeGoals.length)}
            sub={activeGoals.some((g) => g.panel_status === "building") ? "building…" : "up to date"}
          />
        </div>
      </section>

      {/* ── Goal panels ── */}
      <section className="mt-5">
        <h3 className="fd-label mb-2">Your goals</h3>

        <AddGoalInput
          onAdd={(text, sport) => addGoal.mutate({ text, sport })}
          adding={addGoal.isPending}
        />
        {addGoal.isError && (
          <div className="mt-2">
            <ErrorBox
              message={addGoal.error instanceof Error ? addGoal.error.message : "Failed to add goal."}
            />
          </div>
        )}

        <div className="mt-3">
          {goalsQ.isLoading ? (
            <div className="fd-card p-6">
              <Spinner label="Loading your goals…" />
            </div>
          ) : goalsQ.isError ? (
            <ErrorBox
              message={`Couldn't load your goals: ${
                goalsQ.error instanceof Error ? goalsQ.error.message : "unknown error"
              }`}
            />
          ) : activeGoals.length === 0 ? (
            <div className="rounded-card border border-dashed border-border bg-bg-surface/40 px-6 py-10 text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 text-accent">
                <Target size={24} />
              </div>
              <h3 className="text-lg font-semibold text-text-primary">Add your first goal</h3>
              <p className="mt-1 text-sm text-text-muted">
                Pick a type above — your coach builds a panel for it automatically.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {activeGoals.map((goal) =>
                editingId === goal.id ? (
                  <div key={goal.id} className="fd-card p-5 sm:p-6">
                    <label htmlFor={`edit-goal-${goal.id}`} className="fd-label mb-1 block">Edit goal</label>
                    <input
                      id={`edit-goal-${goal.id}`}
                      autoFocus
                      className="fd-input w-full"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitEdit(goal);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      onBlur={() => commitEdit(goal)}
                    />
                    <p className="mt-1.5 text-xs text-text-muted">
                      Enter to save, Esc to cancel. Changing the text rebuilds the panel.
                    </p>
                  </div>
                ) : (
                  <GoalPanel
                    key={goal.id}
                    goal={goal}
                    onRefresh={() => refreshPanel.mutate(goal.id)}
                    onEdit={() => startEdit(goal)}
                    onArchive={() => updateGoal.mutate({ id: goal.id, patch: { status: "archived" } })}
                    refreshing={refreshPanel.isPending && refreshPanel.variables === goal.id}
                  />
                ),
              )}
            </div>
          )}
        </div>
        {deleteGoal.isError && (
          <div className="mt-2">
            <ErrorBox
              message={deleteGoal.error instanceof Error ? deleteGoal.error.message : "Failed to delete goal."}
            />
          </div>
        )}
      </section>

      {/* ── Coach placeholder ── */}
      <section className="mt-5">
        <div className="fd-card fd-card-hover flex items-center gap-4 p-5">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
            <MessageSquare size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-text-primary">Your coach</h3>
            <p className="mt-0.5 text-sm text-text-muted">Plan your week or adjust goals — answers in Chat.</p>
          </div>
          <Link to="/chat" className="fd-btn-primary inline-flex shrink-0 items-center gap-1.5">
            Open Chat <ArrowRight size={16} />
          </Link>
        </div>
      </section>
    </div>
  );
}

// ── Slim athlete greeting (name + avatar only) ────────────────────────────────
// Name/avatar priority: the onboarding profile (what the user actually set for
// themselves) first, then the Strava athlete profile, then a generic fallback —
// since Strava tokens are deployment-global, every tester would otherwise see
// the Strava account owner's identity instead of their own.
function AthleteGreeting({
  profile,
  profileName,
  avatarUrl,
  loading,
}: {
  profile: AthleteProfile;
  profileName?: string;
  avatarUrl?: string | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading athlete…" />;
  const stravaName =
    profile.name || `${profile.firstname || ""} ${profile.lastname || ""}`.trim();
  const name = profileName || stravaName || "Athlete";
  const first = name.split(" ")[0];
  const stravaUrl = profile.profile_url || profile.profile || "";
  const url = avatarUrl || (stravaUrl.startsWith("http") ? stravaUrl : "");

  return (
    <div className="flex items-center gap-4">
      {url ? (
        <img
          src={url}
          alt={name}
          className="h-14 w-14 rounded-full border border-border object-cover"
        />
      ) : (
        <div className="flex h-14 w-14 items-center justify-center rounded-full border border-border bg-bg-surface text-xl font-bold text-text-muted">
          {first.charAt(0).toUpperCase()}
        </div>
      )}
      <div>
        <PageHeader title={`Hi, ${first}`} subtitle="Here's how your goals are tracking." />
      </div>
    </div>
  );
}
