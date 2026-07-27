import { create } from "zustand";

// Global UI state shared across tabs.
interface UiState {
  refreshVersion: number; // bump to force data refetch (sidebar "Refresh data")
  bumpRefresh: () => void;
  sidebarOpen: boolean; // mobile nav drawer (off-canvas below md; always-on at md+)
  setSidebarOpen: (b: boolean) => void;
  toggleSidebar: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  refreshVersion: 0,
  bumpRefresh: () => set((st) => ({ refreshVersion: st.refreshVersion + 1 })),
  sidebarOpen: false,
  setSidebarOpen: (b) => set({ sidebarOpen: b }),
  toggleSidebar: () => set((st) => ({ sidebarOpen: !st.sidebarOpen })),
}));
