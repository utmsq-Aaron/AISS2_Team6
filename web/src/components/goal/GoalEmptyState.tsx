// Dashed-border prompt shown on the Dashboard when no goal is set.

import { Target } from "lucide-react";

export function GoalEmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-card border border-dashed border-border bg-bg-surface/40 px-6 py-10 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 text-accent">
        <Target size={24} />
      </div>
      <h3 className="text-lg font-semibold text-text-primary">
        Set a goal to see your progress.
      </h3>
      <p className="mt-1 text-sm text-text-muted">
        Pick a target — weekly distance, a 5K time, bodyweight — and your coach
        will help you get there.
      </p>
      <button type="button" className="fd-btn-primary mt-4" onClick={onCreate}>
        Set a goal
      </button>
      <p className="mt-3 text-xs text-text-muted">
        …or ask your coach to set one in{" "}
        <span className="font-medium text-text-primary">Chat</span>.
      </p>
    </div>
  );
}
