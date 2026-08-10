// Settings tab — configure all integrations from the UI. Faithful port of
// ui/settings.py onto the FastAPI endpoints (api/routers/settings.py).
//
// OAuth services (Strava, Google) get a "Connect" button that opens the provider's
// auth page in a new tab and polls getSettings until the integration flips true.
// Credential services (Garmin) get a secure form with an optional MFA step.
// API-key services (OpenAI, ORS) get a simple form that saves to .env.

import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { LogOut } from "lucide-react";

import { InfoHint } from "../components/InfoHint";
import { PageHeader } from "../components/PageHeader";
import { Spinner, ErrorBox } from "../components/Spinner";
import { getAthleteOverview, setAthleteProfile } from "../lib/api";
import {
  getSettings,
  getModels,
  putEnv,
  stravaConnect,
  stravaDisconnect,
  stravaToken,
  googleConnect,
  googleDisconnect,
  garminLogin,
  garminLoginStatus,
  garminMfa,
  garminDisconnect,
  tgSession,
  tgDisconnect,
  tgBridge,
  restartServers,
  type SettingsResponse,
  type Integrations,
  type EnvVar,
  type GarminState,
} from "../lib/settingsApi";
import { BORDER, C_GREEN, C_RED, TEXT_PRIMARY, TEXT_MUTED } from "../theme/tokens";

// ── Integration metadata (drives the card layout) — mirrors INTEGRATION_META ─────
interface Meta {
  key: keyof Integrations | "weather";
  label: string;
  icon: string;
  type: "oauth" | "credentials" | "api_key" | "none" | "telegram";
  description: string;
  docsUrl: string;
}

// DISPLAY_ORDER from ui/settings.py: strava, garmin, google, openai, routes, weather, telegram
const META: Meta[] = [
  { key: "strava", label: "Strava", icon: "🏃", type: "oauth", description: "Activities, GPS streams, statistics", docsUrl: "https://www.strava.com/settings/api" },
  { key: "garmin", label: "Garmin Connect", icon: "⌚", type: "credentials", description: "Sleep, HRV, Body Battery, steps", docsUrl: "https://connect.garmin.com" },
  { key: "google", label: "Google Calendar", icon: "📅", type: "oauth", description: "Appointments and training schedule", docsUrl: "https://console.cloud.google.com/apis/credentials" },
  { key: "openai", label: "OpenAI / LLM", icon: "🤖", type: "api_key", description: "LLM for chat and analysis — set model and base URL below", docsUrl: "https://platform.openai.com/api-keys" },
  { key: "routes", label: "OpenRouteService", icon: "🗺️", type: "api_key", description: "Route planning, trail search, isochrones", docsUrl: "https://openrouteservice.org/dev/#/signup" },
  { key: "weather", label: "Open-Meteo", icon: "🌤️", type: "none", description: "Weather, pollen, UV index — no API key needed", docsUrl: "https://open-meteo.com" },
  { key: "telegram", label: "Telegram", icon: "✈️", type: "telegram", description: "Chats, messages, contacts (via external telegram-mcp)", docsUrl: "https://my.telegram.org/apps" },
];

// "garmin" needs a separate "weather" key in Integrations; weather is always-on.
function isConnected(meta: Meta, integ: Integrations): boolean {
  if (meta.type === "none") return true;
  return Boolean(integ[meta.key as keyof Integrations]);
}

// ── Small shared bits ───────────────────────────────────────────────────────────

function EnvRow({ name, env, hint }: { name: string; env: Record<string, EnvVar>; hint: string }) {
  const v = env[name];
  const ok = Boolean(v?.set);
  return (
    <div
      className="flex items-center gap-2 py-1.5"
      style={{ borderBottom: `1px solid ${BORDER}` }}
    >
      <span style={{ color: ok ? C_GREEN : C_RED, fontWeight: 700, fontSize: 13, width: 16 }}>
        {ok ? "✓" : "✗"}
      </span>
      <code style={{ color: TEXT_PRIMARY, fontSize: 13, marginLeft: 4 }}>{name}</code>
      <span style={{ marginLeft: "auto" }}>
        {ok ? (
          <code style={{ color: TEXT_MUTED, fontSize: 11 }}>{v?.value || "set"}</code>
        ) : (
          <span style={{ color: TEXT_MUTED, fontSize: 11 }}>{hint}</span>
        )}
      </span>
    </div>
  );
}

function Toast({ message }: { message: string }) {
  return (
    <div className="mt-2 rounded-lg border border-metric-green/40 bg-metric-green/10 px-3 py-2 text-sm text-metric-green">
      {message}
    </div>
  );
}


// ── Hook: poll getSettings every 2s while a connect flow is pending ──────────────
function usePollSettings(active: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      qc.invalidateQueries({ queryKey: ["settings"] });
    }, 2000);
    return () => window.clearInterval(id);
  }, [active, qc]);
}

// ── Strava (OAuth) ────────────────────────────────────────────────────────────
function StravaCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const connected = data.integrations.strava;
  const hasCreds = data.env.CLIENT_ID?.set && data.env.CLIENT_SECRET?.set;

  const [cid, setCid] = useState("");
  const [csec, setCsec] = useState("");
  const [pending, setPending] = useState(false);
  const [tokenJson, setTokenJson] = useState("");
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  usePollSettings(pending && !connected);

  // When polling detects the connection, stop.
  useEffect(() => {
    if (connected) setPending(false);
  }, [connected]);

  const saveCreds = useMutation({
    mutationFn: () => putEnv({ CLIENT_ID: cid.trim(), CLIENT_SECRET: csec.trim() }),
    onSuccess: () => refetch(),
  });

  const disconnect = useMutation({ mutationFn: stravaDisconnect, onSuccess: () => refetch() });

  const reconnect = useMutation({
    // Reconnect = disconnect then re-open the connect flow.
    mutationFn: async () => {
      await stravaDisconnect();
      const r = await stravaConnect();
      return r;
    },
    onSuccess: (r) => {
      window.open(r.auth_url, "_blank");
      setPending(true);
      refetch();
    },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : String(e)),
  });

  const connect = useMutation({
    mutationFn: stravaConnect,
    onSuccess: (r) => {
      window.open(r.auth_url, "_blank");
      setPending(true);
    },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : String(e)),
  });

  const uploadToken = useMutation({
    mutationFn: () => {
      const parsed = JSON.parse(tokenJson) as Record<string, unknown>;
      return stravaToken(parsed);
    },
    onSuccess: (r) => {
      setTokenMsg(`✅ Connected${r.name ? ` as ${r.name}` : ""}!`);
      setErr(null);
      refetch();
    },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : "Invalid token JSON"),
  });

  if (connected) {
    return (
      <>
        <div className="flex gap-2">
          <button className="fd-btn-secondary flex-1" onClick={() => reconnect.mutate()} disabled={reconnect.isPending}>
            🔄 Reconnect
          </button>
          <button className="fd-btn-secondary flex-1" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
            🔌 Disconnect
          </button>
        </div>
        {err && <ErrorBox message={err} />}
      </>
    );
  }

  if (!hasCreds) {
    return (
      <div className="rounded-lg border border-border bg-bg-surface p-3">
        <p className="mb-2 text-sm font-semibold text-text-primary">🔑 Enter Strava API app credentials</p>
        <p className="mb-2 text-xs text-text-muted">
          These identify the <strong>Strava API application</strong> (not your personal account).
          Create one at strava.com/settings/api — takes about 2 minutes.
        </p>
        <EnvRow name="CLIENT_ID" env={data.env} hint="not set" />
        <EnvRow name="CLIENT_SECRET" env={data.env} hint="not set" />
        <input className="fd-input mt-2 w-full" aria-label="Strava Client ID" placeholder="Client ID" value={cid} onChange={(e) => setCid(e.target.value)} />
        <input className="fd-input mt-2 w-full" type="password" aria-label="Strava Client Secret" placeholder="Client Secret" value={csec} onChange={(e) => setCsec(e.target.value)} />
        <button className="fd-btn-primary mt-2 w-full" onClick={() => saveCreds.mutate()} disabled={saveCreds.isPending || !cid.trim() || !csec.trim()}>
          Save & continue
        </button>
        <a className="mt-2 block text-center text-xs text-accent hover:underline" href="https://www.strava.com/settings/api" target="_blank" rel="noreferrer">
          → Open Strava API page
        </a>
      </div>
    );
  }

  return (
    <>
      {!pending ? (
        <button className="fd-btn-primary w-full" onClick={() => { setErr(null); connect.mutate(); }} disabled={connect.isPending}>
          🔗 Connect with Strava
        </button>
      ) : (
        <div className="rounded-lg border border-border bg-bg-surface p-3">
          <p className="text-sm text-text-primary">🌐 Authorizing on Strava (new tab opened)…</p>
          <p className="mt-1 text-xs text-text-muted">After authorizing, return here — the connection will be detected automatically.</p>
          <button className="fd-btn-secondary mt-2 w-full" onClick={() => refetch()}>🔄 Check connection</button>
        </div>
      )}
      {err && <ErrorBox message={err} />}

      {/* Alternative: paste token JSON */}
      <details className="rounded-lg border border-border bg-bg-surface p-3">
        <summary className="cursor-pointer text-sm text-text-primary">📁 Already have a token? Paste it</summary>
        <p className="mt-2 text-xs text-text-muted">
          Paste the OAuth token JSON (fields: <code>access_token</code>, <code>refresh_token</code>, <code>expires_at</code>,
          optionally <code>athlete</code>). <code>client_id</code> / <code>client_secret</code> are read from .env.
        </p>
        <textarea
          className="fd-input mt-2 h-28 w-full font-mono text-xs"
          aria-label="Strava token JSON"
          placeholder='{"access_token": "...", "refresh_token": "...", "expires_at": 0}'
          value={tokenJson}
          onChange={(e) => setTokenJson(e.target.value)}
        />
        <button className="fd-btn-primary mt-2 w-full" onClick={() => { setTokenMsg(null); uploadToken.mutate(); }} disabled={uploadToken.isPending || !tokenJson.trim()}>
          Save token
        </button>
        {tokenMsg && <Toast message={tokenMsg} />}
      </details>
    </>
  );
}

// ── Google (OAuth) ──────────────────────────────────────────────────────────────
function GoogleCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const connected = data.integrations.google;
  const hasCreds = data.env.GOOGLE_CLIENT_ID?.set && data.env.GOOGLE_CLIENT_SECRET?.set;

  const [cid, setCid] = useState("");
  const [csec, setCsec] = useState("");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  usePollSettings(pending && !connected);
  useEffect(() => { if (connected) setPending(false); }, [connected]);

  const saveCreds = useMutation({
    mutationFn: () => putEnv({ GOOGLE_CLIENT_ID: cid.trim(), GOOGLE_CLIENT_SECRET: csec.trim() }),
    onSuccess: () => refetch(),
  });
  const disconnect = useMutation({ mutationFn: googleDisconnect, onSuccess: () => refetch() });
  const connect = useMutation({
    mutationFn: googleConnect,
    onSuccess: (r) => { window.open(r.auth_url, "_blank"); setPending(true); },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : String(e)),
  });
  const reconnect = useMutation({
    mutationFn: async () => { await googleDisconnect(); return googleConnect(); },
    onSuccess: (r) => { window.open(r.auth_url, "_blank"); setPending(true); refetch(); },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : String(e)),
  });

  if (connected) {
    return (
      <>
        <div className="flex gap-2">
          <button className="fd-btn-secondary flex-1" onClick={() => reconnect.mutate()} disabled={reconnect.isPending}>🔄 Reconnect</button>
          <button className="fd-btn-secondary flex-1" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>🔌 Disconnect</button>
        </div>
        {err && <ErrorBox message={err} />}
      </>
    );
  }

  if (!hasCreds) {
    return (
      <div className="rounded-lg border border-border bg-bg-surface p-3">
        <p className="mb-2 text-sm font-semibold text-text-primary">🔑 Enter API credentials</p>
        <p className="mb-2 text-xs text-text-muted">Create an OAuth project in the Google Cloud Console.</p>
        <EnvRow name="GOOGLE_CLIENT_ID" env={data.env} hint="not set" />
        <EnvRow name="GOOGLE_CLIENT_SECRET" env={data.env} hint="not set" />
        <input className="fd-input mt-2 w-full" aria-label="Google Client ID" placeholder="Client ID" value={cid} onChange={(e) => setCid(e.target.value)} />
        <input className="fd-input mt-2 w-full" type="password" aria-label="Google Client Secret" placeholder="Client Secret" value={csec} onChange={(e) => setCsec(e.target.value)} />
        <button className="fd-btn-primary mt-2 w-full" onClick={() => saveCreds.mutate()} disabled={saveCreds.isPending || !cid.trim() || !csec.trim()}>
          Save & continue
        </button>
        <a className="mt-2 block text-center text-xs text-accent hover:underline" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">
          → Open Google Cloud Console
        </a>
      </div>
    );
  }

  return (
    <>
      {!pending ? (
        <button className="fd-btn-primary w-full" onClick={() => { setErr(null); connect.mutate(); }} disabled={connect.isPending}>
          🔗 Connect with Google
        </button>
      ) : (
        <div className="rounded-lg border border-border bg-bg-surface p-3">
          <p className="text-sm text-text-primary">🌐 Authorizing with Google (new tab opened)…</p>
          <p className="mt-1 text-xs text-text-muted">After authorizing, return here — the connection will be detected automatically.</p>
          <button className="fd-btn-secondary mt-2 w-full" onClick={() => refetch()}>🔄 Check connection</button>
        </div>
      )}
      {err && <ErrorBox message={err} />}
    </>
  );
}

// ── Garmin (credentials + MFA) ───────────────────────────────────────────────────
function GarminCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const mockOn = data.integrations.garmin_mock;
  // Connected for real only when not in mock mode (mock makes integ.garmin true too).
  const connectedReal = data.integrations.garmin && !mockOn;

  const [email, setEmail] = useState(data.env.GARMIN_EMAIL?.set ? data.env.GARMIN_EMAIL.value : "");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [flow, setFlow] = useState<"idle" | GarminState | "mfa_submitted">("idle");
  const [err, setErr] = useState<string | null>(null);

  const setMock = useMutation({
    mutationFn: (on: boolean) => putEnv({ GARMIN_MOCK_HEALTH: on ? "true" : "false" }),
    onSuccess: () => refetch(),
  });

  const disconnect = useMutation({ mutationFn: garminDisconnect, onSuccess: () => { setFlow("idle"); refetch(); } });

  const login = useMutation({
    mutationFn: () => garminLogin(email.trim(), password),
    onSuccess: () => { setFlow("authenticating"); setErr(null); },
    onError: (e: unknown) => { setFlow("error"); setErr(e instanceof Error ? e.message : String(e)); },
  });

  const submitMfa = useMutation({
    mutationFn: () => garminMfa(mfaCode.trim()),
    onSuccess: () => setFlow("mfa_submitted"),
    onError: (e: unknown) => { setFlow("error"); setErr(e instanceof Error ? e.message : String(e)); },
  });

  // Poll login status while authenticating / verifying MFA.
  const polling = flow === "authenticating" || flow === "mfa_submitted";
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    const id = window.setInterval(async () => {
      try {
        const s = await garminLoginStatus();
        if (cancelled) return;
        if (s.state === "mfa_needed") setFlow("mfa_needed");
        else if (s.state === "success") { setFlow("success"); refetch(); }
        else if (s.state === "error") { setErr(s.error || "Unknown error"); setFlow("error"); }
        // "authenticating" → keep polling
      } catch {
        /* transient; keep polling */
      }
    }, 1500);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [polling, refetch]);

  // Once successfully connected, reset to idle.
  useEffect(() => { if (flow === "success") setFlow("idle"); }, [flow]);

  return (
    <>
      {/* Mock-mode toggle */}
      <label className="flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={mockOn}
          onChange={(e) => setMock.mutate(e.target.checked)}
          className="h-4 w-4 accent-accent"
        />
        🔄 Mock mode (demo data — no real device needed)
      </label>

      {mockOn ? (
        <div className="rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-muted">
          Mock mode active — demo data is generated. No Garmin account needed.
        </div>
      ) : connectedReal ? (
        <button className="fd-btn-secondary w-full" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
          🔌 Disconnect Garmin
        </button>
      ) : flow === "idle" || flow === "error" ? (
        <>
          <p className="text-xs text-text-muted">Garmin Connect email and password</p>
          <input className="fd-input w-full" aria-label="Garmin email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input className="fd-input w-full" type="password" aria-label="Garmin password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="fd-btn-primary w-full" onClick={() => login.mutate()} disabled={login.isPending || !email.trim() || !password}>
            Connect
          </button>
          {flow === "error" && err && <ErrorBox message={`Error: ${err}`} />}
        </>
      ) : flow === "authenticating" ? (
        <div className="rounded-lg border border-border bg-bg-surface px-3 py-2">
          <Spinner label="Connecting to Garmin Connect…" />
        </div>
      ) : flow === "mfa_needed" ? (
        <div className="rounded-lg border border-metric-amber/40 bg-metric-amber/10 p-3">
          <p className="mb-2 text-sm text-metric-amber">🔐 Two-factor authentication required</p>
          <input className="fd-input w-full" aria-label="Garmin MFA / OTP Code" placeholder="MFA / OTP Code (123456)" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} />
          <button className="fd-btn-primary mt-2 w-full" onClick={() => submitMfa.mutate()} disabled={submitMfa.isPending || !mfaCode.trim()}>
            Confirm
          </button>
        </div>
      ) : (
        // mfa_submitted
        <div className="rounded-lg border border-border bg-bg-surface px-3 py-2">
          <Spinner label="Verifying MFA…" />
        </div>
      )}
    </>
  );
}

// ── LLM provider + model (KIT ↔ OpenAI official ↔ Gemini) ─────────────────────────
type Prov = "openai" | "openai_official" | "gemini";

function OpenAiCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const qc = useQueryClient();
  const lp = (data.env.LLM_PROVIDER?.value || "openai").toLowerCase();
  const curProvider: Prov =
    ["gemini", "google"].includes(lp) ? "gemini"
      : ["openai_official", "official", "oai"].includes(lp) ? "openai_official"
      : "openai";

  const curKitModel = data.env.AGENT_LLM_MODEL?.value || data.env.AGENT_MODEL?.value || "kit.gpt-4.1";
  const curOfficialModel = data.env.OPENAI_MODEL?.value || "gpt-4o-mini";
  const curGeminiModel = data.env.GEMINI_MODEL?.value || "gemini-2.0-flash";
  const curKitBase = data.env.OPENAI_BASE_URL?.value || "https://ai-gateway.dsi-experimente.de/v1";
  const curOfficialBase = data.env.OPENAI_OFFICIAL_BASE_URL?.value || "";

  const [prov, setProv] = useState<Prov>(curProvider);
  const [kitModel, setKitModel] = useState(curKitModel);
  const [officialModel, setOfficialModel] = useState(curOfficialModel);
  const [geminiModel, setGeminiModel] = useState(curGeminiModel);
  const [kitKey, setKitKey] = useState("");
  const [officialKey, setOfficialKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [kitBase, setKitBase] = useState(curKitBase);
  const [officialBase, setOfficialBase] = useState(curOfficialBase);
  const [msg, setMsg] = useState<string | null>(null);

  // Live model list for the selected provider (falls back to the static lists).
  const staticFor = (p: Prov) =>
    p === "gemini" ? (data.gemini_models ?? [])
      : p === "openai_official" ? (data.openai_models ?? [])
      : (data.models ?? []);
  const modelsQ = useQuery({ queryKey: ["llmModels", prov], queryFn: () => getModels(prov), staleTime: 60_000 });
  const modelOptions = modelsQ.data?.models ?? staticFor(prov);
  const isFallback = modelsQ.data?.source === "fallback";

  const save = useMutation({
    mutationFn: () => {
      const values: Record<string, string> = { LLM_PROVIDER: prov };
      if (prov === "gemini") {
        values.GEMINI_MODEL = geminiModel.trim();
        if (geminiKey.trim()) values.GEMINI_API_KEY = geminiKey.trim();
      } else if (prov === "openai_official") {
        values.OPENAI_MODEL = officialModel.trim();
        if (officialKey.trim()) values.OPENAI_OFFICIAL_API_KEY = officialKey.trim();
        values.OPENAI_OFFICIAL_BASE_URL = officialBase.trim(); // blank → api.openai.com
      } else {
        // KIT / OpenAI-compatible. Set both so the agent layer (prefers
        // AGENT_LLM_MODEL) and the chart service (uses AGENT_MODEL) agree.
        values.AGENT_MODEL = kitModel.trim();
        values.AGENT_LLM_MODEL = kitModel.trim();
        if (kitKey.trim()) values.OPENAI_API_KEY = kitKey.trim();
        if (kitBase.trim()) values.OPENAI_BASE_URL = kitBase.trim();
      }
      return putEnv(values);
    },
    onSuccess: () => {
      setMsg("✅ Saved! Applies on your next message (no restart needed).");
      setKitKey(""); setOfficialKey(""); setGeminiKey("");
      qc.invalidateQueries({ queryKey: ["llmModels"] }); // re-fetch models with the new key
      refetch();
    },
  });

  // Model field: a type-ahead over the live list that ALSO accepts a custom name.
  const modelField = (id: string, label: string, value: string, setValue: (v: string) => void) => (
    <>
      <div className="flex items-center justify-between">
        <label htmlFor={id} className="text-xs text-text-muted">{label}</label>
        <button type="button" className="text-xs text-accent hover:underline disabled:opacity-50"
          onClick={() => modelsQ.refetch()} disabled={modelsQ.isFetching}>
          {modelsQ.isFetching ? "loading…" : "↻ refresh list"}
        </button>
      </div>
      <input id={id} className="fd-input w-full" list="llm-models" value={value}
        placeholder="pick from the list or type a custom model name"
        onChange={(e) => setValue(e.target.value)} />
      <datalist id="llm-models">
        {modelOptions.map((m) => (<option key={m} value={m} />))}
      </datalist>
      <p className="text-[11px] text-text-muted">
        {isFallback
          ? "⚠ couldn't fetch live models (showing a built-in list) — you can still type any model name"
          : `${modelOptions.length} models fetched from the provider · custom names allowed`}
      </p>
    </>
  );

  return (
    <>
      <label htmlFor="llm-provider" className="text-xs text-text-muted">Provider</label>
      <select id="llm-provider" className="fd-input w-full" value={prov} onChange={(e) => setProv(e.target.value as Prov)}>
        <option value="openai">KIT gateway (OpenAI-compatible)</option>
        <option value="openai_official">OpenAI (official)</option>
        <option value="gemini">Google Gemini</option>
      </select>

      {prov === "gemini" && (
        <>
          <input
            className="fd-input w-full"
            type="password"
            aria-label="Gemini API key"
            placeholder={data.env.GEMINI_API_KEY?.set ? data.env.GEMINI_API_KEY.value : "AIza..."}
            value={geminiKey}
            onChange={(e) => setGeminiKey(e.target.value)}
          />
          {modelField("llm-model-gemini", "Gemini model", geminiModel, setGeminiModel)}
        </>
      )}

      {prov === "openai_official" && (
        <>
          <input
            className="fd-input w-full"
            type="password"
            aria-label="OpenAI API key"
            placeholder={data.env.OPENAI_OFFICIAL_API_KEY?.set ? data.env.OPENAI_OFFICIAL_API_KEY.value : "sk-..."}
            value={officialKey}
            onChange={(e) => setOfficialKey(e.target.value)}
          />
          {modelField("llm-model-official", "OpenAI model", officialModel, setOfficialModel)}
          <label htmlFor="llm-official-base" className="text-xs text-text-muted">Base URL (blank = api.openai.com)</label>
          <input id="llm-official-base" className="fd-input w-full" placeholder="https://api.openai.com/v1" value={officialBase} onChange={(e) => setOfficialBase(e.target.value)} />
        </>
      )}

      {prov === "openai" && (
        <>
          <input
            className="fd-input w-full"
            type="password"
            aria-label="OpenAI API key"
            placeholder={data.env.OPENAI_API_KEY?.set ? data.env.OPENAI_API_KEY.value : "sk-..."}
            value={kitKey}
            onChange={(e) => setKitKey(e.target.value)}
          />
          {modelField("llm-model-kit", "Model", kitModel, setKitModel)}
          <label htmlFor="llm-kit-base" className="text-xs text-text-muted">OPENAI_BASE_URL</label>
          <input id="llm-kit-base" className="fd-input w-full" value={kitBase} onChange={(e) => setKitBase(e.target.value)} />
        </>
      )}

      <button className="fd-btn-primary w-full" onClick={() => { setMsg(null); save.mutate(); }} disabled={save.isPending}>
        💾 Save provider &amp; model
      </button>
      {msg && <Toast message={msg} />}
    </>
  );
}

// ── Routes / ORS (api_key) ────────────────────────────────────────────────────
function RoutesCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const connected = data.integrations.routes;
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => putEnv({ ORS_API_KEY: key.trim() }),
    onSuccess: () => { setMsg("✅ Saved!"); setKey(""); refetch(); },
  });
  return (
    <>
      <input
        className="fd-input w-full"
        type="password"
        aria-label="OpenRouteService API key"
        placeholder={data.env.ORS_API_KEY?.set ? data.env.ORS_API_KEY.value : "5b3ce3..."}
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
      <button className="fd-btn-primary w-full" onClick={() => { setMsg(null); save.mutate(); }} disabled={save.isPending || !key.trim()}>
        {connected ? "🔄 Update API key" : "🔑 Enter API key"}
      </button>
      {msg && <Toast message={msg} />}
    </>
  );
}

// ── Telegram (creds form + paste-session + disconnect + bridge) ──────────────────
function TelegramCard({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const connected = data.integrations.telegram;
  const hasCreds = data.env.TELEGRAM_API_ID?.set && data.env.TELEGRAM_API_HASH?.set;

  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [sessionStr, setSessionStr] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const saveCreds = useMutation({
    mutationFn: () => putEnv({ TELEGRAM_API_ID: apiId.trim(), TELEGRAM_API_HASH: apiHash.trim() }),
    onSuccess: () => { setMsg("✅ Saved!"); refetch(); },
  });
  const saveSession = useMutation({
    mutationFn: () => tgSession(sessionStr.trim()),
    onSuccess: () => { setMsg("✅ Session saved!"); setSessionStr(""); refetch(); },
  });
  const disconnect = useMutation({ mutationFn: tgDisconnect, onSuccess: () => refetch() });

  return (
    <>
      {/* API ID / Hash form */}
      <details className="rounded-lg border border-border bg-bg-surface p-3" open={!hasCreds}>
        <summary className="cursor-pointer text-sm text-text-primary">🔑 API ID & Hash</summary>
        <p className="mt-2 text-xs text-text-muted">Create an app at my.telegram.org/apps</p>
        <EnvRow name="TELEGRAM_API_ID" env={data.env} hint="not set" />
        <EnvRow name="TELEGRAM_API_HASH" env={data.env} hint="not set" />
        <input className="fd-input mt-2 w-full" aria-label="Telegram API ID" placeholder="API ID" value={apiId} onChange={(e) => setApiId(e.target.value)} />
        <input className="fd-input mt-2 w-full" type="password" aria-label="Telegram API Hash" placeholder="API Hash" value={apiHash} onChange={(e) => setApiHash(e.target.value)} />
        <button className="fd-btn-primary mt-2 w-full" onClick={() => { setMsg(null); saveCreds.mutate(); }} disabled={saveCreds.isPending || !apiId.trim() || !apiHash.trim()}>
          Save & continue
        </button>
      </details>

      {/* Paste session string */}
      <details className="rounded-lg border border-border bg-bg-surface p-3">
        <summary className="cursor-pointer text-sm text-text-primary">✍️ Paste session string</summary>
        <p className="mt-2 text-xs text-text-muted">
          Generate via CLI: <code>uv run --directory external/telegram-mcp session_string_generator.py</code>
        </p>
        <input className="fd-input mt-2 w-full" type="password" aria-label="Telegram session string" placeholder="TELEGRAM_SESSION_STRING" value={sessionStr} onChange={(e) => setSessionStr(e.target.value)} />
        <button className="fd-btn-primary mt-2 w-full" onClick={() => { setMsg(null); saveSession.mutate(); }} disabled={saveSession.isPending || !sessionStr.trim()}>
          Save session string
        </button>
      </details>

      {connected && (
        <button className="fd-btn-secondary w-full" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
          🔌 Disconnect
        </button>
      )}

      {msg && <Toast message={msg} />}
    </>
  );
}

// ── Telegram bridge control (full-width, below the card) ─────────────────────────
function BridgeControl({ data, refetch }: { data: SettingsResponse; refetch: () => void }) {
  const [running, setRunning] = useState(data.bridge_running);
  const hasCreds = data.integrations.telegram;

  // Keep local state in sync with refetched settings.
  useEffect(() => { setRunning(data.bridge_running); }, [data.bridge_running]);

  const toggle = useMutation({
    mutationFn: (action: "start" | "stop") => tgBridge(action),
    onSuccess: (r) => { setRunning(r.running); refetch(); },
  });

  return (
    <div className="mt-3 border-t border-border pt-3">
      <h4 className="text-base font-semibold text-text-primary">🤖 Telegram Bridge</h4>
      <p className="mt-0.5 text-sm text-text-muted">
        Expose the agent over Telegram. Bridge logs appear in the API process terminal.
      </p>

      {!hasCreds ? (
        <div className="mt-2 rounded-lg border border-metric-amber/40 bg-metric-amber/10 px-3 py-2 text-sm text-metric-amber">
          Telegram credentials missing. Configure API ID, API Hash and Session String above first.
        </div>
      ) : (
        <div className="mt-2 flex items-center gap-3">
          <span
            aria-hidden="true"
            style={{
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: running ? C_GREEN : C_RED,
              display: "inline-block",
            }}
          />
          <span className="flex-1 text-sm font-medium text-text-primary">
            {running ? "Bridge running" : "Bridge stopped"}
          </span>
          {running ? (
            <button className="fd-btn-secondary" onClick={() => toggle.mutate("stop")} disabled={toggle.isPending}>
              ⏹ Stop bridge
            </button>
          ) : (
            <button className="fd-btn-primary" onClick={() => toggle.mutate("start")} disabled={toggle.isPending}>
              ▶ Start bridge
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Weather (none) ────────────────────────────────────────────────────────────
function WeatherCard() {
  return <div className="rounded-lg border border-metric-green/40 bg-metric-green/10 px-3 py-2 text-sm text-metric-green">Active — no setup needed</div>;
}

// ── Developer section (restart MCP servers) ──────────────────────────────────────
function DeveloperSection() {
  const [result, setResult] = useState<string | null>(null);
  const restart = useMutation({
    mutationFn: restartServers,
    onSuccess: (r) => setResult(`Done — stopped ${r.killed}, started ${r.started} servers.`),
    onError: (e: unknown) => setResult(`Error: ${e instanceof Error ? e.message : String(e)}`),
  });
  return (
    <div>
      <h3 className="mb-2 text-lg font-semibold text-text-primary">🔧 Developer</h3>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <p className="text-sm text-text-muted">
          Restart all MCP servers to pick up code changes. Use this after updating server files or when tools show as &quot;Unknown&quot;.
        </p>
        <button className="fd-btn-secondary md:w-auto" onClick={() => { setResult(null); restart.mutate(); }} disabled={restart.isPending}>
          {restart.isPending ? "Restarting…" : "🔄 Restart MCP Servers"}
        </button>
      </div>
      {result && <Toast message={result} />}
    </div>
  );
}

// ── Account (age → literature HR-zone default) ────────────────────────────────
function AccountSection() {
  const ov = useQuery({ queryKey: ["athlete-overview"], queryFn: getAthleteOverview });
  const qc = useQueryClient();
  const [age, setAge] = useState("");
  useEffect(() => {
    const a = ov.data?.profile?.age;
    if (a) setAge(String(a));
  }, [ov.data?.profile?.age]);
  const save = useMutation({
    mutationFn: () => setAthleteProfile(parseInt(age, 10) || 0),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["athlete-overview"] }),
  });
  return (
    <div className="fd-card p-4">
      <div className="mb-2 flex items-center gap-1.5">
        <h3 className="text-sm font-semibold text-text-primary">Account</h3>
        <InfoHint text="Your age sets your default heart-rate zones via the literature formula (208 − 0.7 × age). Log a reference run in the Coach tab to refine them from a measured max HR." />
      </div>
      <label className="fd-label">Age
        <div className="mt-1 flex items-center gap-2">
          <input
            type="number" min={10} max={100} value={age}
            onChange={(e) => setAge(e.target.value)} placeholder="e.g. 30"
            className="fd-input w-28 font-normal normal-case tracking-normal text-text-primary"
          />
          <button
            type="button" onClick={() => save.mutate()} disabled={save.isPending || !age}
            className="fd-btn-secondary text-sm disabled:opacity-40"
          >
            {save.isPending ? "Saving…" : save.isSuccess ? "Saved ✓" : "Save"}
          </button>
        </div>
      </label>
    </div>
  );
}

// ── Compact connection row (status + connect/manage; full flow on demand) ──────
function ConnectionRow({ meta, connected, children }: { meta: Meta; connected: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const none = meta.type === "none";
  return (
    <div className="fd-card overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="text-lg" aria-hidden="true">{meta.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-text-primary">{meta.label}</div>
          <div className="truncate text-xs text-text-muted">{meta.description}</div>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
          none || connected ? "bg-metric-green/10 text-metric-green" : "bg-metric-amber/10 text-metric-amber"
        }`}>
          {none ? "Active" : connected ? "Connected" : "Not connected"}
        </span>
        {!none && (connected ? (
          <button
            type="button" onClick={() => setOpen((o) => !o)} title="Manage / switch account"
            aria-label={`Manage ${meta.label}`}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-surface hover:text-text-primary"
          >
            <LogOut size={16} strokeWidth={2} />
          </button>
        ) : (
          <button type="button" onClick={() => setOpen(true)} className="fd-btn-primary shrink-0 text-xs">
            Connect
          </button>
        ))}
      </div>
      {open && !none && <div className="border-t border-border px-4 py-3">{children}</div>}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export function Settings() {
  const settingsQ = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const qc = useQueryClient();
  // useCallback, not useRef(...).current — a ref read during render is exactly
  // what React tells you not to do, and this only ever needed a stable identity.
  const refetch = useCallback(() => { qc.invalidateQueries({ queryKey: ["settings"] }); }, [qc]);

  if (settingsQ.isLoading) {
    return (
      <div>
        <PageHeader title="⚙️ Integrations" subtitle="Connect your services. Credentials are stored locally in .env and never shared with third parties." />
        <Spinner label="Loading settings…" />
      </div>
    );
  }
  if (settingsQ.error || !settingsQ.data) {
    return (
      <div>
        <PageHeader title="⚙️ Integrations" subtitle="Connect your services." />
        <ErrorBox message={settingsQ.error instanceof Error ? settingsQ.error.message : "Failed to load settings."} />
      </div>
    );
  }

  const data = settingsQ.data;
  const integ = data.integrations;

  // Show every connection compactly (status for all; the manage/connect flow
  // expands on demand). The admin still gets the Developer (restart) section.
  const isAdmin = data.is_admin;
  const cards = META;

  const connectedCount = cards.filter((m) => isConnected(m, integ)).length;

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-1.5">
        <PageHeader title="Settings" subtitle="Connect your services and manage your account." />
        <InfoHint
          className="mt-1"
          label="Privacy"
          text="Credentials are stored locally in .env and never shared with third parties."
        />
      </div>

      <AccountSection />

      <div>
        <div className="fd-label mb-2">Connections · {connectedCount} of {cards.length} connected</div>
        <div className="space-y-2">
          {cards.map((meta) => (
            <div key={meta.key} className="space-y-2">
              <ConnectionRow meta={meta} connected={isConnected(meta, integ)}>
                {meta.key === "strava" && <StravaCard data={data} refetch={refetch} />}
                {meta.key === "garmin" && <GarminCard data={data} refetch={refetch} />}
                {meta.key === "google" && <GoogleCard data={data} refetch={refetch} />}
                {meta.key === "openai" && <OpenAiCard data={data} refetch={refetch} />}
                {meta.key === "routes" && <RoutesCard data={data} refetch={refetch} />}
                {meta.key === "weather" && <WeatherCard />}
                {meta.key === "telegram" && <TelegramCard data={data} refetch={refetch} />}
              </ConnectionRow>
              {meta.key === "telegram" && <BridgeControl data={data} refetch={refetch} />}
            </div>
          ))}
        </div>
      </div>

      {isAdmin && <DeveloperSection />}
    </div>
  );
}
