import { QueryClient } from "@tanstack/react-query";

// Mirrors the Streamlit @st.cache_data behaviour: cache server data, refetch on
// explicit refresh rather than on every focus.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
