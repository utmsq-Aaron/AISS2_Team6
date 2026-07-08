import { useGoals } from "../lib/goalQueries";

// Invisible global poller — keeps goal panels (and coach-added goals) fresh
// across the whole app regardless of which page is showing. The actual polling
// cadence (fast while any panel is building, slow otherwise) lives in useGoals'
// own refetchInterval; this component's only job is to keep that query mounted
// app-wide, mirroring CoachPoll. Mounted once in the authed shell.
export function GoalPoll() {
  useGoals();
  return null;
}
