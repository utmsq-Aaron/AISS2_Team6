import { useQuery } from "@tanstack/react-query";
import { Dumbbell } from "lucide-react";
import { useState } from "react";

import { getKnownUsers, loginUser } from "../lib/api";
import { useAuthStore } from "../store/authStore";

// Quasi-login for the prototype: type (or pick) a known name → get a Bearer token.
// Identity only; everyone sees the same shared data.
export function Login() {
  const setLogin = useAuthStore((s) => s.login);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({ queryKey: ["auth-users"], queryFn: getKnownUsers });
  const users = data?.users ?? [];

  const signIn = async (who: string) => {
    const candidate = who.trim();
    if (!candidate || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { token, user } = await loginUser(candidate);
      setLogin(token, user);
    } catch {
      setError(`Unknown user "${candidate}". Try one of the names below.`);
      setBusy(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center bg-bg-app px-6 text-text-primary">
      <div className="fd-card w-full max-w-sm p-7">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent">
            <Dumbbell size={22} strokeWidth={2} />
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Training Copilot</h1>
            <p className="text-[12px] text-text-muted">Sign in to continue</p>
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void signIn(name);
          }}
        >
          <label className="fd-label" htmlFor="login-name">
            Name
          </label>
          <input
            id="login-name"
            autoFocus
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            placeholder="e.g. Marvin"
            className="fd-input mt-1 w-full"
            disabled={busy}
          />
          {error && <p className="mt-2 text-[12px] text-red-400">{error}</p>}
          <button type="submit" className="fd-btn-primary mt-4 w-full" disabled={busy || !name.trim()}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {users.length > 0 && (
          <div className="mt-6">
            <p className="fd-label mb-2">Quick sign-in</p>
            <div className="flex flex-wrap gap-2">
              {users.map((u) => (
                <button
                  key={u}
                  type="button"
                  onClick={() => {
                    setName(u);
                    void signIn(u);
                  }}
                  disabled={busy}
                  className="fd-btn-secondary px-3 py-1.5 text-sm"
                >
                  {u}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
