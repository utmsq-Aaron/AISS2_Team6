// Thin client over the FastAPI seam (proxied at /api by Vite in dev, by the Node
// BFF in prod). callTool() is the generic data path; streamChat() consumes SSE.

import { forceLogout } from "../store/authStore";

// Auth rides on the httpOnly `fitdash_session` cookie the server set at login;
// same-origin fetches (via the /api proxy) send it automatically, so there are
// no Authorization headers to attach here anymore. A 401 (missing/expired
// session) still drops the user back to the login screen via forceLogout().

export interface ToolResult<T = unknown> {
  name: string;
  ok: boolean;
  data: T;
  text?: boolean;
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) {
    forceLogout(); // token missing/expired → drop back to the login screen
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Auth (email + OTP) ───────────────────────────────────────────────────────

/** POST to an /api/auth route without the 401→logout behavior of http() (there's
 *  no session yet during login). Surfaces FastAPI's `detail` as the error message. */
async function authPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = new Error((data as any)?.detail || `Request failed (${res.status})`);
    (e as any).status = res.status;
    throw e;
  }
  return data as T;
}

/** Request a one-time login code for `email` (emails it). `new_account` hints whether
 *  this email is registering for the first time. */
export const requestOtp = (email: string) =>
  authPost<{ ok: boolean; new_account: boolean; dev_echo?: boolean }>("/auth/request-otp", { email });

/** Verify the code → identity. On success the server ALSO sets the httpOnly
 *  session cookie; the returned `token` is kept for non-browser clients but the
 *  SPA ignores it. Throws (status 400) on a bad code. */
export const verifyOtp = (email: string, code: string) =>
  authPost<{ token: string; user: string; is_admin: boolean; new_account: boolean }>(
    "/auth/verify-otp",
    { email, code },
  );

// ── Chat sessions (persistent, per-user) ──────────────────────────────────────

export interface ChatSummary {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  message_count: number;
  // Coach-chat extras — present only on the pinned system "Coach" entry; absent
  // on normal chats.
  kind?: "coach" | "telegram" | "normal";
  special?: string;
  source?: "telegram" | "coach";
  pinned?: boolean;
  unread?: number;
}

/** Id of the pinned system Coach chat (mirrors the server's fixed id). */
export const COACH_CHAT_ID = "coach";

/** Clear the coach chat's unread counter. Best-effort. */
export const markCoachRead = () =>
  http<{ ok: boolean }>(`/chats/${COACH_CHAT_ID}/read`, { method: "POST", body: "{}" });
export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  ts?: string;
  trace?: ChatTrace;
}
export interface StoredChat {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  messages: StoredMessage[];
}

// ── Training goals (multiple, freeform, per-user) ────────────────────────────
// Each goal is just text (sport-specific goals are common); its dashboard PANEL
// is authored by the agent — a structured spec + a free markdown note — and
// builds in the background after creation/refresh/edit. Poll GET /goals and
// watch panel_status: "empty" → "building" → "ready" | "error".

export type GoalLifecycleStatus = "active" | "achieved" | "archived";
export type GoalSource = "user" | "coach";
export type PanelStatus = "empty" | "building" | "ready" | "error";
/** The panel's HEALTH axis — distinct from Goal.status (the LIFECYCLE axis). */
export type PanelHealthStatus = "on_track" | "at_risk" | "behind" | "reached" | "unknown";

export interface PanelTile {
  label: string;
  value: string;
  sub?: string;
}

export interface PanelProgress {
  pct: number; // 0-100
  label: string;
}

export interface PanelChart {
  kind: "line" | "bar";
  points: { x: string | number; y: number }[];
  y_label?: string;
}

export interface Panel {
  headline: string;
  status: PanelHealthStatus;
  tiles: PanelTile[]; // 2-4 entries
  progress: PanelProgress | null;
  note: string; // markdown
  chart: PanelChart | null;
  generated_at: string; // ISO
}

export interface Goal {
  id: string;
  text: string; // freeform — the goal, in the user's/coach's words
  sport?: string | null;
  source: GoalSource;
  status: GoalLifecycleStatus;
  created_at: string; // ISO
  updated_at: string; // ISO
  panel: Panel | null;
  panel_status: PanelStatus;
  panel_updated_at: string | null;
}

/** GET /goals — never 404s; `{goals: []}` for a new user. */
export const listGoals = () => http<{ goals: Goal[] }>("/goals").then((r) => r.goals);

/** POST /goals — create a goal from freeform text; its panel builds in the background. */
export const addGoal = (text: string, sport?: string) =>
  http<Goal>("/goals", { method: "POST", body: JSON.stringify({ text, sport }) });

/** PATCH /goals/{id} — update text/sport/status (send only what changed). */
export const updateGoal = (
  id: string,
  patch: Partial<Pick<Goal, "text" | "sport" | "status">>,
) => http<Goal>(`/goals/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

/** DELETE /goals/{id} — permanently remove a goal. */
export const deleteGoal = (id: string) =>
  http<{ ok: boolean }>(`/goals/${id}`, { method: "DELETE" });

/** POST /goals/{id}/refresh — kick a background panel rebuild from fresh data. */
export const refreshGoalPanel = (id: string) =>
  http<{ ok: boolean }>(`/goals/${id}/refresh`, { method: "POST", body: "{}" });

export const listChats = () => http<{ chats: ChatSummary[] }>("/chats").then((r) => r.chats);
export const createChat = () => http<StoredChat>("/chats", { method: "POST", body: "{}" });
export const getChat = (id: string) => http<StoredChat>(`/chats/${id}`);
export const renameChat = (id: string, title: string) =>
  http<{ ok: boolean }>(`/chats/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const deleteChat = (id: string) =>
  http<{ ok: boolean }>(`/chats/${id}`, { method: "DELETE" });

/** Call an MCP tool by namespaced name `server__tool`. Returns parsed JSON data. */
export async function callTool<T = unknown>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const r = await http<ToolResult<T>>("/tools/call", {
    method: "POST",
    body: JSON.stringify({ name, args }),
  });
  return r.data;
}

/** Fetch the standalone 3D flythrough HTML page for an activity (authenticated).
 *  The React side renders it in an `<iframe srcdoc>`; the in-page Export button
 *  encodes an MP4 client-side. Returns the raw HTML string. */
export async function fetchFlythroughHtml(
  activityId: number,
  opts: { mode?: string; orientation?: string; resolution?: string; duration?: number } = {},
): Promise<string> {
  const qs = new URLSearchParams();
  if (opts.mode) qs.set("mode", opts.mode);
  if (opts.orientation) qs.set("orientation", opts.orientation);
  if (opts.resolution) qs.set("resolution", opts.resolution);
  if (opts.duration) qs.set("duration", String(opts.duration));
  const res = await fetch(`/api/flythrough/${activityId}?${qs.toString()}`);
  if (res.status === 401) {
    forceLogout();
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Flythrough ${res.status}: ${detail}`);
  }
  return res.text();
}

// ── Profile (name, avatar, first-login onboarding) ───────────────────────────

export interface Profile {
  name: string;
  onboarding_complete: boolean;
  has_avatar: boolean;
}

/** GET /profile — never 404s; defaults for a brand-new user. */
export const getProfile = () => http<Profile>("/profile");

/** PUT /profile — send only what changed. */
export const putProfile = (patch: Partial<Pick<Profile, "name" | "onboarding_complete">>) =>
  http<Profile>("/profile", { method: "PUT", body: JSON.stringify(patch) });

/** POST /profile/avatar — multipart upload. Does NOT go through http() (that
 *  forces Content-Type: application/json) and does NOT set Content-Type manually
 *  either — the browser must generate its own multipart boundary. */
export async function uploadAvatar(file: File): Promise<Profile> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/profile/avatar", {
    method: "POST",
    body: form,
  });
  if (res.status === 401) {
    forceLogout();
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Avatar upload ${res.status}: ${detail}`);
  }
  return res.json();
}

/** GET /profile/avatar — raw authed fetch (mirrors fetchFlythroughHtml's auth
 *  pattern). 404 (no avatar set) → null instead of throwing. */
export async function fetchAvatarBlob(): Promise<Blob | null> {
  const res = await fetch("/api/profile/avatar");
  if (res.status === 404) return null;
  if (res.status === 401) {
    forceLogout();
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) return null;
  return res.blob();
}

export interface ServerStatus {
  key: string;
  label: string;
  server_up: boolean;
  service_ok: boolean;
}
export interface HealthResponse {
  garmin_mock: boolean;
  servers: ServerStatus[];
}

export const getServerHealth = () => http<HealthResponse>("/health/servers");
/** Re-discover MCP tools on the orchestrator (POST /chat/refresh-tools). */
export const refreshChatTools = () => http<{ count: number }>("/chat/refresh-tools", { method: "POST" });
export const getConfigIssues = () => http<{ issues: string[] }>("/health/config");
export const getSettings = () => http<SettingsResponse>("/settings");
export const putEnv = (values: Record<string, string>) =>
  http<{ written: string[] }>("/settings/env", { method: "PUT", body: JSON.stringify({ values }) });

export interface SettingsResponse {
  integrations: Record<string, boolean>;
  env: Record<string, { set: boolean; value: string; secret: boolean }>;
}

// ── Chat SSE ──────────────────────────────────────────────────────────────────

export type ChatMessage = { role: "user" | "assistant"; content: string };

/** A long-running background analysis kicked off by a turn. Its finished report
 *  arrives LATER as new assistant message(s) in the Coach chat. */
export interface BackgroundJobAction {
  type: "background_job";
  job_id: string;
  topic: string;
}
/** A 3D-flythrough action lifted from a `prepare_flythrough` tool result (see
 *  core/agent_trace.flythrough_from_results). `hidden` keeps it out of the debug
 *  trace; the Chat renderer reads it to offer a "Watch flythrough" card. */
export interface FlythroughAction {
  type: "flythrough";
  activity_id: number | string;
  activity_name?: string;
  mode?: string;
  duration_sec?: number;
  orientation?: string;
  resolution?: string;
  hidden?: boolean;
}
/** A trace action — `background_job` (deep-work signal), `flythrough`, or any other
 *  action the server emits. Kept loose so we can read `.type` without an exhaustive
 *  union. */
export type TraceAction = (BackgroundJobAction | FlythroughAction | { type?: string }) &
  Record<string, unknown>;

export interface ChatTrace {
  run_id?: string;
  question?: string;
  answer?: string;
  plan?: { reasoning?: string; steps?: Array<{ tool: string; args?: unknown; label?: string }> };
  tool_calls?: Array<{ tool: string; label?: string; duration_ms?: number; error?: string | null }>;
  timing?: Record<string, number>;
  agents?: Array<{ agent: string; phase: number; duration_ms: number; data_summary?: string }>;
  route_data?: { tool: string; data: Record<string, unknown> } | null;
  chart_hints?: string[];
  actions?: TraceAction[];
  error?: string | null;
}

export interface ChatHandlers {
  onStatus?: (message: string) => void;
  onToken?: (delta: string) => void;
  onReset?: () => void;
  onTrace?: (trace: ChatTrace) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/** POST a chat turn and consume the SSE stream. Returns an abort function.
 *  When `chatId` is set the server loads history from (and persists the turn to)
 *  that stored chat. */
export function streamChat(
  message: string,
  history: ChatMessage[],
  handlers: ChatHandlers,
  chatId?: string,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history, chat_id: chatId }),
        signal: controller.signal,
      });
      if (res.status === 401) {
        forceLogout();
        throw new Error("Session expired — please log in again.");
      }
      if (!res.ok || !res.body) throw new Error(`chat ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // Parse the SSE framing: blocks separated by a blank line, each with an
      // `event:` line and one or more `data:` lines.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          if (!block.trim()) continue;
          let event = "message";
          const dataLines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          const raw = dataLines.join("\n");
          let data: any = {};
          try {
            data = raw ? JSON.parse(raw) : {};
          } catch {
            data = { raw };
          }
          switch (event) {
            case "status": handlers.onStatus?.(data.message); break;
            case "token": handlers.onToken?.(data.delta); break;
            case "reset": handlers.onReset?.(); break;
            case "trace": handlers.onTrace?.(data as ChatTrace); break;
            case "error": handlers.onError?.(data.message); break;
            case "done": handlers.onDone?.(); break;
          }
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError?.(err instanceof Error ? err.message : String(err));
        handlers.onDone?.();
      }
    }
  })();

  return () => controller.abort();
}

/** Generate LLM charts for a finished chat turn → array of Plotly figure specs. */
export async function generateCharts(trace: ChatTrace): Promise<any[]> {
  const r = await http<{ figures: any[] }>("/charts", {
    method: "POST",
    body: JSON.stringify({ trace }),
  });
  return r.figures || [];
}

// ── Feedback (tester bug-report button) ──────────────────────────────────────
// The server captures the full diagnostic bundle (logs, chats, etc.) itself;
// the frontend only sends the report text plus cheap client-side context.

/** POST /feedback — submit a tester bug report. Empty/whitespace `text` → 422. */
export const submitFeedback = (text: string, context?: Record<string, unknown>) =>
  http<{ ok: boolean; bundle_id: string }>("/feedback", {
    method: "POST",
    body: JSON.stringify({ text, context }),
  });

// ── Athlete (structured race goal + zones + training plan — the Coach tab) ───
// Fronted by api/routers/athlete.py over the athlete MCP server. All numbers
// (zones, prognosis, week volumes) are computed deterministically server-side;
// plan GENERATION runs on the coach agent — poll the overview while
// plan_generation === "running".

// ONE main goal drives the training plan; any number of MILESTONES mark the way
// there — a real tune-up/minor race (kind "race") or a non-race training
// checkpoint (kind "checkpoint", e.g. "first 15 km long run"). Milestones never
// alter the plan; race-kind ones softly inform nearby workout choice. Separate
// from the freeform dashboard goals (core.goal_store).
export interface RaceGoal {
  id?: string;
  name: string;
  date: string; // ISO
  distance_km: number;
  target_time: string | null;
  is_main?: boolean;
  kind?: "race" | "checkpoint";
  source?: "user" | "coach";
  status?: "pending" | "achieved";
  note?: string | null;
  days_to_race?: number;
}

export interface TimelineEvent {
  id: string;
  type: "injury" | "illness" | "race" | "note";
  title: string;
  start_date: string;
  end_date: string | null;
  severity: string | null;
  blocked_sports: string[];
}

// Zones use the German bands ReKom/GA1/GA2/WSA (keys of the band records),
// grounded in the textbook corpus (%HFmax + Karvonen/HFR; pace = factor × race
// pace). See docs/trainingsregeln.md.
export interface ZoneSet {
  hr?: {
    bands_bpm: Record<string, [number, number]>;      // %HFmax bands
    bands_bpm_hfr?: Record<string, [number, number]>; // Karvonen/%HFR bands (if resting HR)
    method?: string;
    basis: string;
    hfr_basis?: string;
  };
  pace?: { bands_pace: Record<string, [string, string]>; race_pace?: string; basis: string };
  pace_source?: { distance_km: number; time_secs: number; label: string | null };
  hr_max_used?: number;
  hr_max_estimated?: boolean;
  computed_at?: string;
}

export interface PlanWorkout {
  day: string;
  title: string;
  sport: string;
  zone: string;
  duration_min?: number;
  distance_km?: number;
  pace_range?: string;
  hr_range?: string;
  structure?: string;
  why?: string;
  source?: string;
}

export interface PlanWeek {
  week: number;
  phase: "base" | "build" | "peak" | "taper";
  start_date: string;
  target_km: number;
  cutback?: boolean;
  sessions?: number;
  workouts: PlanWorkout[];
}

export interface TrainingPlan {
  race?: RaceGoal;
  weeks: PlanWeek[];
  n_weeks?: number;
  current_week?: number | null;
  status?: string;
  saved_at?: string;
}

export interface AthleteOverview {
  user: string;
  profile: {
    race?: RaceGoal;            // the main goal (drives the plan)
    races?: RaceGoal[];         // main goal + all milestones
    weekly_sessions?: number;
    preferred_days?: string[];
    age?: number;
  };
  timeline: TimelineEvent[];
  zones: ZoneSet;
  plan: TrainingPlan | null;
  days_to_race?: number;
  // Prognosis is EITHER a benchmark comparison (real race near the goal distance)
  // OR a note asking for a benchmark — no distance extrapolation (docs/trainingsregeln.md §3).
  prognosis?: {
    benchmark?: string;
    benchmark_pace?: string;
    required_pace?: string | null;
    on_track?: boolean | null;
    basis?: string;
    note?: string;
  };
  plan_generation?: string | null; // "running" | "error: …" | null
}

export const getAthleteOverview = () => http<AthleteOverview>("/athlete/overview");

/** POST /athlete/profile — store the athlete's age (drives the literature HFmax default). */
export const setAthleteProfile = (age: number) =>
  http<{ ok: boolean }>("/athlete/profile", { method: "POST", body: JSON.stringify({ age }) });

/** POST /athlete/goal — set (replace) the MAIN goal. Clears the stored plan. */
export const setRaceGoal = (body: {
  race_name: string; race_date: string; distance_km: number;
  target_time?: string; weekly_sessions?: number; preferred_days?: string;
}) => http<{ ok: boolean }>("/athlete/goal", { method: "POST", body: JSON.stringify(body) });

/** POST /athlete/milestone — add a milestone (race tune-up or non-race checkpoint). */
export const addMilestone = (body: {
  title: string; target_date: string; kind: "race" | "checkpoint";
  distance_km?: number; target_time?: string; note?: string;
}) => http<{ ok: boolean }>("/athlete/milestone", { method: "POST", body: JSON.stringify({ ...body, source: "user" }) });

/** PATCH /athlete/milestone/{id} — mark a milestone pending/achieved. */
export const updateMilestoneStatus = (id: string, status: "pending" | "achieved") =>
  http<{ ok: boolean }>(`/athlete/milestone/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });

/** DELETE /athlete/milestone/{id} — remove the main goal or a milestone. Deleting
 * the main goal also clears the stored plan (it was built for that goal). */
export const deleteMilestone = (id: string) =>
  http<{ ok: boolean }>(`/athlete/milestone/${id}`, { method: "DELETE" });

export const addTimelineEvent = (body: {
  event_type: string; title: string; start_date: string;
  end_date?: string; severity?: string; blocked_sports?: string;
}) => http<{ ok: boolean }>("/athlete/timeline", { method: "POST", body: JSON.stringify(body) });

export const deleteTimelineEvent = (id: string) =>
  http<{ ok: boolean }>(`/athlete/timeline/${id}`, { method: "DELETE" });

export const generatePlan = () =>
  http<{ ok: boolean; status: string }>("/athlete/plan/generate", { method: "POST" });
