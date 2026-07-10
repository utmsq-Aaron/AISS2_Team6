// The sole goal-creation UI — a single freeform text box (+ optional sport chip)
// replacing the old structured metric/target/unit/deadline form. No modal: goals
// are just text ("Sub-40 10K by December"), so there is nothing to pick from.

import { Plus } from "lucide-react";
import { useState } from "react";

export function AddGoalInput({
  onAdd,
  adding,
}: {
  onAdd: (text: string, sport?: string) => void;
  adding?: boolean;
}) {
  const [text, setText] = useState("");
  const [sport, setSport] = useState("");

  function submit() {
    const t = text.trim();
    if (!t || adding) return;
    onAdd(t, sport.trim() || undefined);
    setText("");
    setSport("");
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="fd-card flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
      <input
        className="fd-input flex-1"
        aria-label="New goal"
        placeholder="Add a goal — e.g. 'Sub-40 10K by December'"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={adding}
      />
      <input
        className="fd-input w-full sm:w-40"
        aria-label="Sport (optional)"
        placeholder="Sport (optional)"
        value={sport}
        onChange={(e) => setSport(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={adding}
      />
      <button
        type="button"
        className="fd-btn-primary inline-flex shrink-0 items-center justify-center gap-1.5"
        onClick={submit}
        disabled={adding || !text.trim()}
      >
        <Plus size={16} /> {adding ? "Adding…" : "Add goal"}
      </button>
    </div>
  );
}
