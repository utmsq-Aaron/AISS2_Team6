import { create } from "zustand";

// Auth state for the email + OTP login.
//
// The session token is NO LONGER handled by JavaScript: on login the server sets
// it as an httpOnly `fitdash_session` cookie that the browser attaches to every
// same-origin /api request automatically. JS can't read it, so an XSS can no
// longer exfiltrate a durable 30-day credential from localStorage.
//
// We keep only the user's email and admin flag in localStorage — non-sensitive
// UI HINTS so the App gate stays synchronous (no boot-time /auth/me round-trip or
// loading flash). isAdmin here is display-only; the server enforces admin access
// per-route via require_admin. Their validity is checked lazily by the existing
// 401 → forceLogout handling on any real API call.
//
// Residual risks of the underlying stateless-token design (see api/auth.py
// current_user for the full list): httpOnly stops durable token THEFT, not live
// in-page abuse by an XSS; the token has no server-side revocation and stays
// valid until it expires (up to 30 days); and it still appears once in the
// verify-otp JSON response.

const USER_KEY = "fitdash.user";
const ADMIN_KEY = "fitdash.admin";

// One-time purge: remove any token left in a tester's browser by the old
// localStorage-based auth. Runs at module load; harmless once the key is gone.
localStorage.removeItem("fitdash.token");

interface AuthState {
  user: string | null;
  isAdmin: boolean;
  login: (user: string, isAdmin: boolean) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: localStorage.getItem(USER_KEY),
  isAdmin: localStorage.getItem(ADMIN_KEY) === "1",
  login: (user, isAdmin) => {
    localStorage.setItem(USER_KEY, user);
    localStorage.setItem(ADMIN_KEY, isAdmin ? "1" : "0");
    set({ user, isAdmin });
  },
  logout: () => {
    // Best-effort: ask the server to clear the httpOnly cookie (JS can't). Fire
    // and forget — the local state clears regardless of whether it succeeds.
    fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ADMIN_KEY);
    set({ user: null, isAdmin: false });
  },
}));

/** Force a logout from non-React code (e.g. on a 401 response). */
export const forceLogout = (): void => useAuthStore.getState().logout();
