// Centered modal hosting the GoalForm over a dimmed backdrop (mirrors the overlay
// approach in FlythroughModal — Esc/backdrop-click close).

import { useEffect } from "react";
import { X } from "lucide-react";

import type { Goal, GoalInput } from "../../lib/api";
import { GoalForm } from "./GoalForm";

export function GoalFormModal({
  initial,
  onSubmit,
  onClose,
  saving,
  error,
}: {
  initial?: Goal | null;
  onSubmit: (v: GoalInput) => void;
  onClose: () => void;
  saving?: boolean;
  error?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 px-4 py-8 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="fd-card w-full max-w-lg p-5 sm:p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">
            {initial ? "Edit goal" : "Set a goal"}
          </h2>
          <button
            type="button"
            className="fd-btn-ghost -mr-2 p-1.5"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <GoalForm
          initial={initial}
          onSubmit={onSubmit}
          onCancel={onClose}
          saving={saving}
          error={error}
        />
      </div>
    </div>
  );
}
