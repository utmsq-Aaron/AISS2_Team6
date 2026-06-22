import { create } from "zustand";

// Global UI state shared across tabs — mirrors the Streamlit sidebar controls.
interface UiState {
  sportFilter: string; // "All" or a Strava sport_type
  setSportFilter: (s: string) => void;
  refreshVersion: number; // bump to force data refetch (sidebar "Refresh data")
  bumpRefresh: () => void;
  sidebarOpen: boolean; // mobile nav drawer (off-canvas below md; always-on at md+)
  setSidebarOpen: (b: boolean) => void;
  toggleSidebar: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  sportFilter: "All",
  setSportFilter: (s) => set({ sportFilter: s }),
  refreshVersion: 0,
  bumpRefresh: () => set((st) => ({ refreshVersion: st.refreshVersion + 1 })),
  sidebarOpen: false,
  setSidebarOpen: (b) => set({ sidebarOpen: b }),
  toggleSidebar: () => set((st) => ({ sidebarOpen: !st.sidebarOpen })),
}));

export const SPORT_TYPES = [
  "All", "Run", "Ride", "Hike", "Walk", "Swim", "Workout", "WeightTraining",
  "EBikeRide", "VirtualRide", "NordicSki", "AlpineSki",
];
