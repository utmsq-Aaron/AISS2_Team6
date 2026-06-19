// Settings tab API client — mirrors the http() pattern in lib/api.ts (kept local;
// api.ts's http() is module-private). Typed functions for api/routers/settings.py.

// ── Local fetch helper (6-line pattern copied from api.ts) ──────────────────────
async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
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
