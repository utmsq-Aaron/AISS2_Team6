// Reusable goals block used in two modes:
//   • Coach (editable): a collapsed "Add goal" builder + full agent-authored panels.
//   • Dashboard (readOnly): compact status cards that link into the Coach.
// Goals live in one place (the Coach) and are only mirrored, read-only, elsewhere.

import { Plus, Target } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { Goal } from "../../lib/api";
import {
  useAddGoal,
  useGoals,
  useRefreshGoalPanel,
  useUpdateGoal,
} from "../../lib/goalQueries";
import { InfoHint } from "../InfoHint";
import { ErrorBox, Spinner } from "../Spinner";
import { AddGoalInput } from "./AddGoalInput";
import { GoalPanel } from "./GoalPanel";

const GOALS_INFO = "Open-ended personal goals — separate from your main race goal and " +
  "milestones above. Each gets its own AI-tracked progress panel (e.g. \"swim 3x/week\").";

export function GoalsSection({ readOnly = false }: { readOnly?: boolean }) {
  const goalsQ = useGoals();
  const addGoal = useAddGoal();
  const updateGoal = useUpdateGoal();
  const refreshPanel = useRefreshGoalPanel();
  const [showBuilder, setShowBuilder] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  const active = (goalsQ.data ?? []).filter((g) => g.status === "active");

  // ── Read-only (Dashboard): compact cards linking to the Coach ──
  if (readOnly) {
    return (
      <section>
        <div className="mb-2 flex items-center gap-1">
          <h3 className="fd-label">Your goals</h3>
          <InfoHint text={GOALS_INFO} />
        </div>
        {active.length === 0 ? (
          <Link
            to="/coach"
            className="fd-card fd-card-hover flex items-center gap-3 px-4 py-3 text-sm text-text-muted"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/10 text-accent">
              <Target size={16} />
            </span>
            No goals yet — set one in the Coach tab.
          </Link>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {active.map((g) => (
              <Link
                key={g.id}
                to="/coach"
                className="fd-card fd-card-hover flex items-center justify-between gap-3 px-4 py-3"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-text-primary">{g.text}</div>
                  {g.panel?.headline && (
                    <div className="truncate text-xs text-text-muted">{g.panel.headline}</div>
                  )}
                </div>
                <span className="shrink-0 text-xs font-medium text-accent">Open →</span>
              </Link>
            ))}
          </div>
        )}
      </section>
    );
  }

  // ── Editable (Coach): collapsed builder + full panels ──
  function commitEdit(goal: Goal) {
    const text = editText.trim();
    setEditingId(null);
    if (text && text !== goal.text) updateGoal.mutate({ id: goal.id, patch: { text } });
  }

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-semibold text-text-primary">Your goals</h2>
          <InfoHint text={GOALS_INFO} />
        </div>
        <button
          type="button"
          onClick={() => setShowBuilder((s) => !s)}
          className="fd-btn-ghost inline-flex items-center gap-1.5 text-xs"
        >
          <Plus size={14} /> {showBuilder ? "Close" : "Add goal"}
        </button>
      </div>

      {showBuilder && (
        <div className="mb-3">
          <AddGoalInput
            onAdd={(text, sport) => {
              addGoal.mutate({ text, sport });
              setShowBuilder(false);
            }}
            adding={addGoal.isPending}
          />
        </div>
      )}
      {addGoal.isError && (
        <div className="mb-3">
          <ErrorBox message={addGoal.error instanceof Error ? addGoal.error.message : "Couldn't add goal."} />
        </div>
      )}

      {goalsQ.isLoading ? (
        <div className="fd-card p-6"><Spinner label="Loading your goals…" /></div>
      ) : active.length === 0 ? (
        <div className="rounded-card border border-dashed border-border bg-bg-surface/40 px-6 py-8 text-center text-sm text-text-muted">
          No goals yet — click <span className="font-medium text-text-primary">Add goal</span> to set one.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {active.map((goal) =>
            editingId === goal.id ? (
              <div key={goal.id} className="fd-card p-5">
                <label className="fd-label mb-1 block">Edit goal</label>
                <input
                  autoFocus
                  className="fd-input w-full"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitEdit(goal);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  onBlur={() => commitEdit(goal)}
                />
                <p className="mt-1.5 text-xs text-text-muted">Enter to save, Esc to cancel.</p>
              </div>
            ) : (
              <GoalPanel
                key={goal.id}
                goal={goal}
                onRefresh={() => refreshPanel.mutate(goal.id)}
                onEdit={() => {
                  setEditingId(goal.id);
                  setEditText(goal.text);
                }}
                onArchive={() => updateGoal.mutate({ id: goal.id, patch: { status: "archived" } })}
                refreshing={refreshPanel.isPending && refreshPanel.variables === goal.id}
              />
            ),
          )}
        </div>
      )}
    </section>
  );
}
