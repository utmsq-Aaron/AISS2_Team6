// Settings tab API client — mirrors the http() pattern in lib/api.ts (kept local;
// api.ts's http() is module-private). Typed functions for api/routers/settings.py.

import { forceLogout } from "../store/authStore";

// ── Local fetch helper (6-line pattern copied from api.ts) ──────────────────────
// Auth rides on the httpOnly session cookie (same-origin), so no headers to add.
async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) {
    forceLogout();
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Response types ──────────────────────────────────────────────────────────────

export interface Integrations {
  strava: boolean;
  garmin: boolean;
  garmin_mock: boolean;
  google: boolean;
  routes: boolean;
  telegram: boolean;
  openai: boolean;
}

export interface EnvVar {
  set: boolean;
  value: string;
  secret: boolean;
}

export interface SettingsResponse {
  integrations: Integrations;
  is_admin: boolean;
  env: Record<string, EnvVar>;
  models: string[];
  gemini_models: string[];
  openai_models: string[];
  bridge_running: boolean;
}

export type GarminState = "authenticating" | "mfa_needed" | "success" | "error";

export interface GarminLoginStatus {
  state: GarminState;
  error?: string;
}

export interface AuthUrlResponse {
  auth_url: string;
}

export interface StravaTokenResult {
  name?: string;
}

export interface TgSendCodeResponse {
  inter: string;
  code_hash: string;
}

export interface TgSignInResponse {
  status: "ok" | "password";
  session?: string;
  inter?: string;
}

export interface BridgeStatusResponse {
  running: boolean;
}

export interface RestartResult {
  killed: number;
  started: number;
}

// ── Core ────────────────────────────────────────────────────────────────────────

export interface ModelsResponse {
  models: string[];
  source: "live" | "fallback";
  error?: string;
}

export const getSettings = () => http<SettingsResponse>("/settings");

// Live model list for a provider ("openai" | "openai_official" | "gemini").
export const getModels = (provider: string) =>
  http<ModelsResponse>(`/settings/models/${provider}`);

export const putEnv = (values: Record<string, string>) =>
  http<{ written: string[] }>("/settings/env", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });

// ── Strava ────────────────────────────────────────────────────────────────────

export const stravaConnect = () =>
  http<AuthUrlResponse>("/settings/strava/connect", { method: "POST" });

export const stravaDisconnect = () =>
  http<{ ok: boolean }>("/settings/strava/disconnect", { method: "POST" });

export const stravaToken = (token: Record<string, unknown>) =>
  http<StravaTokenResult>("/settings/strava/token", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

// ── Google ──────────────────────────────────────────────────────────────────

export const googleConnect = () =>
  http<AuthUrlResponse>("/settings/google/connect", { method: "POST" });

export const googleDisconnect = () =>
  http<{ ok: boolean }>("/settings/google/disconnect", { method: "POST" });

// ── Garmin ──────────────────────────────────────────────────────────────────

export const garminLogin = (email: string, password: string) =>
  http<{ state: "authenticating" }>("/settings/garmin/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const garminLoginStatus = () =>
  http<GarminLoginStatus>("/settings/garmin/login/status");

export const garminMfa = (code: string) =>
  http<{ ok: boolean }>("/settings/garmin/mfa", {
    method: "POST",
    body: JSON.stringify({ code }),
  });

export const garminDisconnect = () =>
  http<{ ok: boolean }>("/settings/garmin/disconnect", { method: "POST" });

// ── Telegram ──────────────────────────────────────────────────────────────────

export const tgSendCode = (phone: string) =>
  http<TgSendCodeResponse>("/settings/telegram/send-code", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });

export const tgSignIn = (inter: string, phone: string, code: string, codeHash: string) =>
  http<TgSignInResponse>("/settings/telegram/sign-in", {
    method: "POST",
    body: JSON.stringify({ inter, phone, code, code_hash: codeHash }),
  });

export const tgPassword = (inter: string, password: string) =>
  http<{ status: "ok" }>("/settings/telegram/password", {
    method: "POST",
    body: JSON.stringify({ inter, password }),
  });

export const tgSession = (session: string) =>
  http<{ ok: boolean }>("/settings/telegram/session", {
    method: "POST",
    body: JSON.stringify({ session }),
  });

export const tgDisconnect = () =>
  http<{ ok: boolean }>("/settings/telegram/disconnect", { method: "POST" });

export const tgBridge = (action: "start" | "stop") =>
  http<BridgeStatusResponse>("/settings/telegram/bridge", {
    method: "POST",
    body: JSON.stringify({ action }),
  });

export const tgBridgeStatus = () =>
  http<BridgeStatusResponse>("/settings/telegram/bridge/status");

// ── MCP servers ────────────────────────────────────────────────────────────

export const restartServers = () =>
  http<RestartResult>("/settings/servers/restart", { method: "POST" });

// ── Garmin → Strava sync ──────────────────────────────────────────────────────

export interface SyncActivity {
  id: number;
  name: string;
  type: string;
  date: string;
  distance_km: number;
  duration_s: number;
  /** null when the Strava side could not be read — then "missing" is unknowable. */
  in_strava: boolean | null;
}

export interface SyncPreview {
  activities: SyncActivity[];
  missing: SyncActivity[];
  /** False when the Strava fetch returned nothing — treat `missing` as unusable. */
  has_matches: boolean;
  start: string;
  end: string;
  days: number;
}

export const syncPreview = (days: number) =>
  http<SyncPreview>("/sync/preview", {
    method: "POST",
    body: JSON.stringify({ days }),
  });

export interface SyncResult {
  status: "ok" | "duplicate" | "skipped" | "error";
  name: string;
  message?: string;
  url?: string;
  index: number;
  total: number;
}

export interface SyncCounts {
  ok: number;
  duplicate: number;
  skipped: number;
  error: number;
}

export interface SyncHandlers {
  onProgress?: (name: string, index: number, total: number) => void;
  onResult?: (r: SyncResult) => void;
  onSummary?: (c: SyncCounts) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/**
 * Upload the given Garmin activities to Strava, reporting each one as it lands.
 * SSE rather than a plain POST: Strava processes uploads asynchronously and we
 * poll each for up to a minute, so a batch easily outlives a request timeout.
 * Returns an abort function.
 */
export function syncExport(
  activities: SyncActivity[],
  handlers: SyncHandlers,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch("/api/sync/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          activities: activities.map((a) => ({ id: a.id, name: a.name, date: a.date })),
        }),
        signal: controller.signal,
      });
      if (res.status === 401) {
        forceLogout();
        throw new Error("Session expired — please log in again.");
      }
      if (!res.ok || !res.body) throw new Error(`sync ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
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
          let data: Record<string, unknown> = {};
          try {
            data = raw ? JSON.parse(raw) : {};
          } catch {
            data = {};
          }
          switch (event) {
            case "progress":
              handlers.onProgress?.(
                String(data.name ?? ""),
                Number(data.index ?? 0),
                Number(data.total ?? 0),
              );
              break;
            case "result": handlers.onResult?.(data as unknown as SyncResult); break;
            case "summary": handlers.onSummary?.(data as unknown as SyncCounts); break;
            case "error": handlers.onError?.(String(data.message ?? "")); break;
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
