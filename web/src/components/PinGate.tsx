import { Lock } from "lucide-react";
import { useEffect, useState } from "react";

// Shared PIN gate — the first wall in front of the whole app when the BFF runs
// with DO_LOCK=true (public deployments). It asks /bff/status; if the deployment
// is locked and this browser hasn't passed the PIN yet, it shows a PIN screen and
// posts /bff/login. The BFF rate-limits + signs the session cookie (see server/),
// so this is just the UI for that gate. In dev (no BFF) /bff/status is absent, so
// we fail OPEN and never block.

type Gate = "checking" | "open" | "locked";

export function PinGate({ children }: { children: React.ReactNode }) {
  const [gate, setGate] = useState<Gate>("checking");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    try {
      const r = await fetch("/bff/status", { credentials: "include" });
      if (!r.ok) return setGate("open"); // no gate configured → don't block
      const d = await r.json();
      setGate(d.locked && !d.authed ? "locked" : "open");
    } catch {
      setGate("open"); // BFF absent (Vite dev) → don't block
    }
  }
  useEffect(() => {
    void check();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!pin.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/bff/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (r.ok) {
        setPin("");
        setGate("open");
        return;
      }
      if (r.status === 429) {
        const retry = r.headers.get("Retry-After");
        setError(`Too many attempts — try again${retry ? ` in ${retry}s` : " later"}.`);
      } else {
        setError("Incorrect PIN.");
      }
    } catch {
      setError("Network error — try again.");
    } finally {
      setBusy(false);
    }
  }

  if (gate === "checking") return null; // brief; avoids a flash of the app/login
  if (gate === "open") return <>{children}</>;

  return (
    <div className="flex h-screen items-center justify-center bg-bg-app px-6 text-text-primary">
      <div className="fd-card w-full max-w-sm p-7">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent">
            <Lock size={20} strokeWidth={2} />
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Restricted</h1>
            <p className="text-[12px] text-text-muted">Enter the access PIN to continue</p>
          </div>
        </div>

        <form onSubmit={submit}>
          <label className="fd-label" htmlFor="pin">
            Access PIN
          </label>
          <input
            id="pin"
            type="password"
            autoFocus
            autoComplete="off"
            value={pin}
            onChange={(e) => {
              setPin(e.target.value);
              setError(null);
            }}
            placeholder="••••••••"
            className="fd-input mt-1 w-full"
            disabled={busy}
          />
          {error && <p className="mt-2 text-[12px] text-red-400">{error}</p>}
          <button type="submit" className="fd-btn-primary mt-4 w-full" disabled={busy || !pin.trim()}>
            {busy ? "Checking…" : "Unlock"}
          </button>
        </form>
      </div>
    </div>
  );
}
