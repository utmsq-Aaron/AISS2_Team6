import { useQueryClient } from "@tanstack/react-query";

import { SPORT_TYPES, useUiStore } from "../store/uiStore";
import { StatusDots } from "./StatusDots";

// Mirrors the Streamlit sidebar: title, live status dots, sport filter, refresh.
export function Sidebar() {
  const qc = useQueryClient();
  const { sportFilter, setSportFilter, bumpRefresh } = useUiStore();

  const refresh = () => {
    bumpRefresh();
    qc.invalidateQueries();
  };

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col gap-4 border-r border-border bg-bg-sidebar px-4 py-5">
      <div>
        <h1 className="text-lg font-bold text-text-primary">🏋️ Training Copilot</h1>
        <p className="text-xs text-text-muted">AI-powered sports analytics</p>
      </div>

      <hr className="border-border" />

      <StatusDots />

      <hr className="border-border" />

      <div>
        <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          Filter
        </h3>
        <select
          value={sportFilter}
          onChange={(e) => setSportFilter(e.target.value)}
          className="fd-input w-full text-sm"
        >
          {SPORT_TYPES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <button onClick={refresh} className="fd-btn-secondary w-full text-sm">
        🔄 Refresh data
      </button>

      <div className="mt-auto text-xs text-text-muted">Training Copilot · AISS2 Team 6</div>
    </aside>
  );
}
