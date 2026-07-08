import { Dumbbell, Mail } from "lucide-react";
import { useState } from "react";

import { requestOtp, verifyOtp } from "../lib/api";
import { useAuthStore } from "../store/authStore";

// Email + OTP login/registration. Step 1: enter email → a code is emailed. Step 2:
// enter the code → you're signed in (and registered, if it's your first time).
export function Login() {
  const setLogin = useAuthStore((s) => s.login);
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [isNew, setIsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await requestOtp(email.trim());
      setIsNew(r.new_account);
      setStep("code");
      setNotice(`We sent a 6-digit code to ${email.trim()}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the code.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await verifyOtp(email.trim(), code.trim());
      setLogin(r.token, r.user, r.is_admin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg-app px-6 text-text-primary">
      <div className="fd-card w-full max-w-sm p-7">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent">
            <Dumbbell size={22} strokeWidth={2} />
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Training Copilot</h1>
            <p className="text-[12px] text-text-muted">
              {step === "email" ? "Sign in or register with your email" : "Enter your login code"}
            </p>
          </div>
        </div>

        {step === "email" ? (
          <form onSubmit={sendCode}>
            <label className="fd-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoFocus
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setError(null);
              }}
              placeholder="you@example.com"
              className="fd-input mt-1 w-full"
              disabled={busy}
            />
            {error && <p className="mt-2 text-[12px] text-red-400">{error}</p>}
            <button type="submit" className="fd-btn-primary mt-4 w-full" disabled={busy || !email.trim()}>
              {busy ? "Sending…" : "Send code"}
            </button>
            <p className="mt-3 text-[11px] text-text-muted">
              First time? Entering your code creates your account.
            </p>
          </form>
        ) : (
          <form onSubmit={verify}>
            {notice && (
              <p className="mb-3 flex items-center gap-1.5 text-[12px] text-text-muted">
                <Mail size={13} strokeWidth={2} /> {notice}
              </p>
            )}
            <label className="fd-label" htmlFor="code">
              {isNew ? "Code (creates your account)" : "Login code"}
            </label>
            <input
              id="code"
              inputMode="numeric"
              autoFocus
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => {
                setCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                setError(null);
              }}
              placeholder="123456"
              className="fd-input mt-1 w-full tracking-[0.4em]"
              disabled={busy}
            />
            {error && <p className="mt-2 text-[12px] text-red-400">{error}</p>}
            <button type="submit" className="fd-btn-primary mt-4 w-full" disabled={busy || code.length < 6}>
              {busy ? "Verifying…" : isNew ? "Create account" : "Sign in"}
            </button>
            <div className="mt-3 flex items-center justify-between text-[11px] text-text-muted">
              <button
                type="button"
                className="hover:text-text-primary"
                onClick={() => {
                  setStep("email");
                  setCode("");
                  setError(null);
                  setNotice(null);
                }}
              >
                ← Change email
              </button>
              <button type="button" className="hover:text-text-primary" disabled={busy} onClick={() => sendCode()}>
                Resend code
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
