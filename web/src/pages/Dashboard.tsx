// Dashboard — a minimal, goal-centric home. The dense analytics that used to live
// here (period selector, activity search, 5-col metric grid, activity map + delete
// + flythrough, recent activities, training charts, official Strava stats) now
// live on the Analysis page. This page is: a slim athlete greeting, the goal
// centrepiece, an "are you on track" signals row, and a coach placeholder.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, MessageSquare } from "lucide-react";

import { GoalCard } from "../components/goal/GoalCard";
import { GoalEmptyState } from "../components/goal/GoalEmptyState";
import { GoalFormModal } from "../components/goal/GoalFormModal";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { Spinner, ErrorBox } from "../components/Spinner";
import { callTool } from "../lib/api";
import { useGoal, useGoalProgress, usePutGoal } from "../lib/goalQueries";
import { formatMetricValue } from "../lib/goalFormat";
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

export function Dashboard() {
  const refreshVersion = useUiStore((s) => s.refreshVersion);
  const [modalOpen, setModalOpen] = useState(false);

  // ── Goal ──
  const goalQ = useGoal();
  const progressQ = useGoalProgress();
  const putGoal = usePutGoal();

  // ── Athlete (name + avatar only) ──
  const athleteQ = useQuery({
    queryKey: ["athlete", refreshVersion],
    queryFn: () => callTool<AthleteResult>("strava__get_athlete_profile", {}),
  });
  const profile = athleteQ.data?.profile ?? {};
  const coords = {
    lat: profile.lat ?? KARLSRUHE.lat,
    lon: profile.lon ?? KARLSRUHE.lon,
  };

  // ── Weather (one compact current-conditions card) ──
  const weatherQ = useQuery({
    queryKey: ["dash-weather", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<CurrentWeather>("weather__get_current_weather", coords),
  });

  const goal = goalQ.data ?? null;
  const progress = progressQ.data ?? null;

  function handleSubmit(v: Parameters<typeof putGoal.mutate>[0]) {
    putGoal.mutate(v, { onSuccess: () => setModalOpen(false) });
  }

  return (
    <div>
      {modalOpen && (
        <GoalFormModal
          initial={goal}
          onSubmit={handleSubmit}
          onClose={() => setModalOpen(false)}
          saving={putGoal.isPending}
          error={
            putGoal.isError
              ? putGoal.error instanceof Error
                ? putGoal.error.message
                : "Failed to save goal."
              : undefined
          }
        />
      )}

      <AthleteGreeting profile={profile} loading={athleteQ.isLoading} />

      {/* ── Goal centrepiece ── */}
      <section className="mt-4">
        {goalQ.isLoading ? (
          <div className="fd-card p-6">
            <Spinner label="Loading your goal…" />
          </div>
        ) : goalQ.isError ? (
          <ErrorBox
            message={`Couldn't load your goal: ${
              goalQ.error instanceof Error ? goalQ.error.message : "unknown error"
            }`}
          />
        ) : goal ? (
          <GoalCard
            goal={goal}
            progress={progress}
            loading={progressQ.isFetching}
            onEdit={() => setModalOpen(true)}
          />
        ) : (
          <GoalEmptyState onCreate={() => setModalOpen(true)} />
        )}
      </section>

      {/* ── "Are you on track" signals ── */}
      <section className="mt-5">
        <h3 className="fd-label mb-2">This week at a glance</h3>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <GoalSignal goal={goal} progress={progress} />
          <PaceSignal progress={progress} />
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
            label="Deadline"
            value={
              goal?.deadline
                ? new Date(goal.deadline).toLocaleDateString(undefined, {
                    day: "numeric",
                    month: "short",
                  })
                : "—"
            }
            sub={
              progress?.status === "reached"
                ? "goal reached 🎉"
                : goal?.deadline
                  ? "target date"
                  : "no goal set"
            }
          />
        </div>
      </section>

      {/* ── Coach placeholder ── */}
      <section className="mt-5">
        <div className="fd-card fd-card-hover flex items-center gap-4 p-5">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
            <MessageSquare size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-text-primary">Your coach</h3>
            <p className="mt-0.5 text-sm text-text-muted">
              Ask about your training, plan the week ahead, or adjust your goal — your
              coach reads your data and answers in Chat.
            </p>
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
function AthleteGreeting({
  profile,
  loading,
}: {
  profile: AthleteProfile;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading athlete…" />;
  const name =
    profile.name ||
    `${profile.firstname || ""} ${profile.lastname || ""}`.trim() ||
    "Athlete";
  const first = name.split(" ")[0];
  const url = profile.profile_url || profile.profile || "";

  return (
    <div className="flex items-center gap-4">
      {url.startsWith("http") ? (
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
        <PageHeader title={`Hi, ${first}`} subtitle="Here's how your goal is tracking." />
      </div>
    </div>
  );
}

// ── Signal cards derived from goal progress ───────────────────────────────────
function GoalSignal({
  goal,
  progress,
}: {
  goal: ReturnType<typeof useGoal>["data"];
  progress: ReturnType<typeof useGoalProgress>["data"];
}) {
  if (!goal) {
    return <MetricCard label="Goal progress" value="—" sub="no goal set" />;
  }
  const pct = progress?.pct;
  const current = progress?.current;
  return (
    <MetricCard
      label="Goal progress"
      value={pct != null ? `${Math.round(pct)}%` : "—"}
      sub={
        current != null
          ? `now ${formatMetricValue(goal.metric, current)}`
          : "no data yet"
      }
    />
  );
}

function PaceSignal({
  progress,
}: {
  progress: ReturnType<typeof useGoalProgress>["data"];
}) {
  const onTrack = progress?.on_track;
  const status = progress?.status;
  const value =
    status === "reached"
      ? "Reached 🎉"
      : onTrack == null
        ? "—"
        : onTrack
          ? "On pace"
          : "Behind pace";
  return (
    <MetricCard
      label="On track?"
      value={value}
      deltaColor={
        status === "reached" || onTrack ? "green" : onTrack === false ? "red" : "muted"
      }
      sub={progress ? "vs. what's needed" : "set a goal to see"}
    />
  );
}
