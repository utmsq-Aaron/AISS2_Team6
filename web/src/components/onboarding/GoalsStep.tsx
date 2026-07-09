// Step 3 — freeform training goals, reusing the exact same input + mutation used
// on the Dashboard/Settings goal pages. Goals are persisted immediately by the
// mutation, so both Continue and Skip just advance — nothing pending to save.

import { Target } from "lucide-react";

import { AddGoalInput } from "../goal/AddGoalInput";
import { useAddGoal, useGoals } from "../../lib/goalQueries";
import { ErrorBox, Spinner } from "../Spinner";

export function GoalsStep({ onNext }: { onNext: () => void }) {
  const goalsQuery = useGoals();
  const addGoal = useAddGoal();

  const activeGoals = (goalsQuery.data ?? []).filter((g) => g.status === "active");

  return (
    <div className="flex flex-col text-center">
      <span className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
        <Target size={28} strokeWidth={2} />
      </span>
      <h1 className="text-xl font-semibold text-text-primary">What are you training for?</h1>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-muted">
        Tell me a goal — anything from &quot;sub-40 10K&quot; to &quot;just move more this
        month&quot; — and I&apos;ll personalize your coaching around it. Each goal gets its own
        dashboard panel that builds itself in the background.
      </p>

      <div className="mt-6 text-left">
        <AddGoalInput
          onAdd={(text, sport) => addGoal.mutate({ text, sport })}
          adding={addGoal.isPending}
        />
        {addGoal.isError && (
          <div className="mt-2">
            <ErrorBox
              message={addGoal.error instanceof Error ? addGoal.error.message : "Could not add that goal."}
            />
          </div>
        )}
      </div>

      <div className="mt-4 text-left">
        {goalsQuery.isLoading ? (
          <Spinner label="Loading your goals…" />
        ) : activeGoals.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {activeGoals.map((g) => (
              <li
                key={g.id}
                className="flex items-center gap-2 rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                <span className="truncate">{g.text}</span>
                {g.sport && <span className="fd-label ml-auto shrink-0">{g.sport}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-center text-xs text-text-muted">
            No goals yet — add one above, or skip and add one later from the Dashboard.
          </p>
        )}
      </div>

      <div className="mt-6 flex w-full gap-3">
        <button type="button" className="fd-btn-secondary flex-1" onClick={onNext}>
          Skip
        </button>
        <button type="button" className="fd-btn-primary flex-1" onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  );
}
