// Thin client over the FastAPI seam (proxied at /api by Vite in dev, by the Node
// BFF in prod). callTool() is the generic data path; streamChat() consumes SSE.

import { authToken, forceLogout } from "../store/authStore";

/** Bearer header for the logged-in user (empty before login / on /auth/login). */
export function authHeaders(): Record<string, string> {
  const t = authToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export interface ToolResult<T = unknown> {
  name: string;
  ok: boolean;
  data: T;
  text?: boolean;
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders() },
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

/** Verify the code → Bearer token + identity. Throws (status 400) on a bad code. */
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

// ── Training goal (persistent, per-user) ─────────────────────────────────────

/** A metric the user can set a goal against. */
export type GoalMetric =
  | "weekly_distance_km"
  | "total_distance_km"
  | "5k_time"
  | "bodyweight_kg";
export type GoalUnit = "km" | "mm:ss" | "kg";
export type GoalDirection = "increase" | "decrease" | "maintain";
export type GoalSource = "form" | "coach";

/** The saved goal object. `target` is seconds for the `5k_time` metric. */
export interface Goal {
  id: string;
  title: string;
  why?: string;
  metric: GoalMetric;
  target: number;
  unit: GoalUnit;
  direction: GoalDirection;
  deadline: string | null; // ISO date
  baseline?: number;
  status: string;
  source: GoalSource;
  created_at: string;
  updated_at: string;
}

/** The editable subset of a goal (what PUT /goals accepts). */
export type GoalInput = Omit<Goal, "id" | "source" | "created_at" | "updated_at">;

export type GoalProgressStatus =
  | "on_track"
  | "at_risk"
  | "behind"
  | "reached"
  | "unknown"
  | "no_goal";

export interface GoalProgress {
  status: GoalProgressStatus;
  current?: number;
  target?: number;
  unit?: GoalUnit;
  pct?: number;
  on_track?: boolean;
  delta_needed?: number;
  direction?: GoalDirection;
}

/** GET /goals — an empty `{}` (no `id`) or a 404 both mean "no goal set" → null. */
export async function getGoal(): Promise<Goal | null> {
  try {
    const g = await http<Goal | Record<string, never>>("/goals");
    return g && "id" in g && g.id ? (g as Goal) : null;
  } catch (e) {
    if (e instanceof Error && /API 404/.test(e.message)) return null;
    throw e;
  }
}

/** PUT /goals — save (create or update) the goal; returns the saved object. */
export const putGoal = (input: Partial<GoalInput>) =>
  http<Goal>("/goals", { method: "PUT", body: JSON.stringify(input) });

/** DELETE /goals — clear the goal. */
export const deleteGoal = () => http<{ ok: boolean }>("/goals", { method: "DELETE" });

/** GET /goals/progress — `status:"no_goal"` → null. */
export async function getGoalProgress(): Promise<GoalProgress | null> {
  const p = await http<GoalProgress>("/goals/progress");
  return p.status === "no_goal" ? null : p;
}

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
  const res = await fetch(`/api/flythrough/${activityId}?${qs.toString()}`, {
    headers: { ...authHeaders() },
  });
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
/** A trace action — `background_job` (deep-work signal) or any other action the
 *  server emits. Kept loose so we can read `.type` without an exhaustive union. */
export type TraceAction = (BackgroundJobAction | { type?: string }) & Record<string, unknown>;

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
        headers: { "Content-Type": "application/json", ...authHeaders() },
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
