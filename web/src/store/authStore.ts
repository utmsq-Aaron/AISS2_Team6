import { create } from "zustand";

// Auth state for the email + OTP login. The Bearer token, the user's email, and
// whether they're the admin are persisted to localStorage so a refresh keeps you
// signed in. Identity only — the token tells the API who is asking; only the admin
// may open Settings.

const TOKEN_KEY = "fitdash.token";
const USER_KEY = "fitdash.user";
const ADMIN_KEY = "fitdash.admin";

interface AuthState {
  token: string | null;
  user: string | null;
  isAdmin: boolean;
  login: (token: string, user: string, isAdmin: boolean) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: localStorage.getItem(USER_KEY),
  isAdmin: localStorage.getItem(ADMIN_KEY) === "1",
  login: (token, user, isAdmin) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, user);
    localStorage.setItem(ADMIN_KEY, isAdmin ? "1" : "0");
    set({ token, user, isAdmin });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ADMIN_KEY);
    set({ token: null, user: null, isAdmin: false });
  },
}));

/** Read the token outside React (e.g. in the api client). */
export const authToken = (): string | null => localStorage.getItem(TOKEN_KEY);

/** Force a logout from non-React code (e.g. on a 401 response). */
export const forceLogout = (): void => useAuthStore.getState().logout();
