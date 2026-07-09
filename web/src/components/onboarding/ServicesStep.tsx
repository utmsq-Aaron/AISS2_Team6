// Step 4 (final) — compact connect cards for Strava / Google Calendar / Garmin,
// reusing the exact polling/state-machine pattern proven in pages/Settings.tsx
// (StravaCard/GoogleCard/GarminCard) but at a smaller, inline scale. This step's
// primary button is NOT "Continue" — it's "Let's go", and Skip performs the SAME
// finish action (mark onboarding_complete) since there's no further step to reach.

import { Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { putProfile } from "../../lib/api";
import {
  garminLogin,
  garminLoginStatus,
  garminMfa,
  getSettings,
  googleConnect,
  stravaConnect,
  type GarminState,
} from "../../lib/settingsApi";
import { ErrorBox } from "../Spinner";

// ── Shared bits ───────────────────────────────────────────────────────────────

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span
      className={
        "inline-flex h-2 w-2 shrink-0 rounded-full " + (connected ? "bg-metric-green" : "bg-text-muted/40")
      }
    />
  );
}

function MiniCard({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-border bg-bg-surface p-3">{children}</div>;
}

/** Poll GET /settings every ~3s while `active`; used by the OAuth cards so the
 *  connected state is picked up automatically after the provider tab returns. */
function usePollIntegrations(active: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      qc.invalidateQueries({ queryKey: ["onboarding-settings"] });
    }, 3000);
    return () => window.clearInterval(id);
  }, [active, qc]);
}

// ── Strava ──────────────────────────────────────────────────────────────────

function StravaMiniCard({ connected }: { connected: boolean }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePollIntegrations(pending && !connected);
  useEffect(() => {
    if (connected) setPending(false);
  }, [connected]);

  const connect = useMutation({
    mutationFn: stravaConnect,
    onSuccess: (r) => {
      window.open(r.auth_url, "_blank");
      setPending(true);
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <MiniCard>
      <div className="flex items-center gap-2">
        <span className="text-lg">🏃</span>
        <span className="flex-1 text-sm font-medium text-text-primary">Strava</span>
        <StatusDot connected={connected} />
        <span className="text-xs text-text-muted">{connected ? "Connected" : "Not connected"}</span>
      </div>
      {!connected && (
        <button
          type="button"
          className="fd-btn-secondary mt-2 w-full py-1.5 text-sm"
          onClick={() => { setError(null); connect.mutate(); }}
          disabled={connect.isPending || pending}
        >
          {pending ? "Waiting for authorization…" : "Connect Strava"}
        </button>
      )}
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
    </MiniCard>
  );
}

// ── Google Calendar ───────────────────────────────────────────────────────────

function GoogleMiniCard({ connected }: { connected: boolean }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePollIntegrations(pending && !connected);
  useEffect(() => {
    if (connected) setPending(false);
  }, [connected]);

  const connect = useMutation({
    mutationFn: googleConnect,
    onSuccess: (r) => {
      window.open(r.auth_url, "_blank");
      setPending(true);
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <MiniCard>
      <div className="flex items-center gap-2">
        <span className="text-lg">📅</span>
        <span className="flex-1 text-sm font-medium text-text-primary">Google Calendar</span>
        <StatusDot connected={connected} />
        <span className="text-xs text-text-muted">{connected ? "Connected" : "Not connected"}</span>
      </div>
      {!connected && (
        <button
          type="button"
          className="fd-btn-secondary mt-2 w-full py-1.5 text-sm"
          onClick={() => { setError(null); connect.mutate(); }}
          disabled={connect.isPending || pending}
        >
          {pending ? "Waiting for authorization…" : "Connect Google Calendar"}
        </button>
      )}
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
    </MiniCard>
  );
}

// ── Garmin ──────────────────────────────────────────────────────────────────

function GarminMiniCard({ connected }: { connected: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [flow, setFlow] = useState<"idle" | GarminState>("idle");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const login = useMutation({
    mutationFn: () => garminLogin(email.trim(), password),
    onSuccess: () => { setFlow("authenticating"); setError(null); },
    onError: (e: unknown) => { setFlow("error"); setError(e instanceof Error ? e.message : String(e)); },
  });

  // Poll login status every ~2s while authenticating / awaiting MFA. Stops once
  // resolved (success/error) or on unmount.
  const polling = flow === "authenticating" || flow === "mfa_needed";
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    const id = window.setInterval(async () => {
      try {
        const s = await garminLoginStatus();
        if (cancelled) return;
        if (s.state === "mfa_needed") setFlow("mfa_needed");
        else if (s.state === "success") {
          setFlow("success");
          qc.invalidateQueries({ queryKey: ["onboarding-settings"] });
        } else if (s.state === "error") {
          setError(s.error || "Unknown error");
          setFlow("error");
        }
        // "authenticating" → keep polling
      } catch {
        /* transient; keep polling */
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [polling, qc]);

  const submitMfa = useMutation({
    mutationFn: (code: string) => garminMfa(code),
    onSuccess: () => setFlow("authenticating"), // keep polling; status will flip to success/error
    onError: (e: unknown) => { setFlow("error"); setError(e instanceof Error ? e.message : String(e)); },
  });

  const connectedNow = connected || flow === "success";

  return (
    <MiniCard>
      <div className="flex items-center gap-2">
        <span className="text-lg">⌚</span>
        <span className="flex-1 text-sm font-medium text-text-primary">Garmin</span>
        <StatusDot connected={connectedNow} />
        <span className="text-xs text-text-muted">{connectedNow ? "Connected" : "Not connected"}</span>
      </div>

      {!connectedNow && (flow === "idle" || flow === "error") && (
        <div className="mt-2 flex flex-col gap-1.5">
          <input
            className="fd-input w-full py-1.5 text-sm"
            placeholder="Garmin email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="fd-input w-full py-1.5 text-sm"
            type="password"
            placeholder="Garmin password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            className="fd-btn-secondary w-full py-1.5 text-sm"
            onClick={() => login.mutate()}
            disabled={login.isPending || !email.trim() || !password}
          >
            Connect
          </button>
          {error && <ErrorBox message={error} />}
        </div>
      )}

      {!connectedNow && flow === "authenticating" && (
        <div className="mt-2 flex items-center gap-2 text-xs text-text-muted">
          <Loader2 size={14} className="animate-spin" /> Connecting to Garmin…
        </div>
      )}

      {!connectedNow && flow === "mfa_needed" && (
        <div className="mt-2 flex flex-col gap-1.5">
          <p className="text-xs text-metric-amber">Two-factor code required</p>
          <input
            className="fd-input w-full py-1.5 text-sm"
            placeholder="123456"
            value={mfaCode}
            onChange={(e) => setMfaCode(e.target.value)}
          />
          <button
            type="button"
            className="fd-btn-secondary w-full py-1.5 text-sm"
            onClick={() => submitMfa.mutate(mfaCode.trim())}
            disabled={submitMfa.isPending || !mfaCode.trim()}
          >
            Verify
          </button>
        </div>
      )}
    </MiniCard>
  );
}

// ── Step shell ────────────────────────────────────────────────────────────────

export function ServicesStep({ onFinish }: { onFinish: () => void }) {
  const settingsQuery = useQuery({
    queryKey: ["onboarding-settings"],
    queryFn: getSettings,
    staleTime: 0,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const integrations = settingsQuery.data?.integrations;

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await putProfile({ onboarding_complete: true });
      onFinish();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not finish onboarding.");
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col text-center">
      <h1 className="text-xl font-semibold text-text-primary">Connect your services</h1>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-muted">
        The more I can see, the better a coach I can be. Connect what you&apos;ve got — skip the
        rest for now, you can always add them later in Settings.
      </p>

      <div className="mt-6 flex flex-col gap-3 text-left">
        <StravaMiniCard connected={Boolean(integrations?.strava)} />
        <GoogleMiniCard connected={Boolean(integrations?.google)} />
        <GarminMiniCard connected={Boolean(integrations?.garmin)} />
      </div>

      <p className="mt-4 text-[11px] text-text-muted">
        Heads up: these connections apply to this whole FitDash instance, not just your account.
      </p>

      {error && <div className="mt-3"><ErrorBox message={error} /></div>}

      <div className="mt-6 flex w-full gap-3">
        <button type="button" className="fd-btn-secondary flex-1" onClick={finish} disabled={busy}>
          Skip
        </button>
        <button
          type="button"
          className="fd-btn-primary inline-flex flex-1 items-center justify-center gap-1.5"
          onClick={finish}
          disabled={busy}
        >
          {busy ? "Finishing…" : (<><Check size={16} strokeWidth={2} /> Let&apos;s go</>)}
        </button>
      </div>
    </div>
  );
}
