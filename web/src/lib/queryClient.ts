import { QueryClient } from "@tanstack/react-query";

// Cache server data and refetch on
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
