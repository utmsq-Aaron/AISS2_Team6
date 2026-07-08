import { useQuery } from "@tanstack/react-query";

import { useChatStore } from "../store/chatStore";

// Invisible global poller — keeps the Coach chat's unread badge (and, when the
// coach chat is active, its turns) fresh across the whole app. Overrides the
// global "no refetch on focus" default so a returning user sees fresh coach
// state. Mounted once in the authed shell.
export function CoachPoll() {
  useQuery({
    queryKey: ["coach-poll"],
    queryFn: async () => {
      await useChatStore.getState().pollCoach();
      return Date.now();
    },
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 0,
    enabled: true,
  });
  return null;
}
