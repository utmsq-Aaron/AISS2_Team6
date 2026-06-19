import { create } from "zustand";

// Quasi-login state for the prototype. The Bearer token (and the display name) are
// persisted to localStorage so a refresh keeps you signed in. Identity only — the
// token just tells the API who is asking; everyone sees the same shared data.

const TOKEN_KEY = "fitdash.token";
const USER_KEY = "fitdash.user";

interface AuthState {
  token: string | null;
  user: string | null;
  login: (token: string, user: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: localStorage.getItem(USER_KEY),
  login: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, user);
    set({ token, user });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, user: null });
  },
}));

/** Read the token outside React (e.g. in the api client). */
export const authToken = (): string | null => localStorage.getItem(TOKEN_KEY);

/** Force a logout from non-React code (e.g. on a 401 response). */
export const forceLogout = (): void => useAuthStore.getState().logout();
