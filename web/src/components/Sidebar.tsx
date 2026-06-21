import { useQueryClient } from "@tanstack/react-query";
import { Dumbbell, RefreshCw } from "lucide-react";
import { NavLink } from "react-router-dom";

import { visibleNav } from "../nav";
import { useAuthStore } from "../store/authStore";
import { SPORT_TYPES, useUiStore } from "../store/uiStore";
import { StatusDots } from "./StatusDots";

// Sidebar — focused on global navigation + real-time service status (design IA),
// with the sport filter and data refresh as secondary controls below.
export function Sidebar() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const nav = visibleNav(isAdmin);
  const { sportFilter, setSportFilter, bumpRefresh } = useUiStore();

  const refresh = () => {
    bumpRefresh();
    qc.invalidateQueries();
  };

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col gap-5 overflow-y-auto border-r border-border bg-bg-sidebar px-3 py-5">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent/15 text-accent">
          <Dumbbell size={20} strokeWidth={2} />
        </span>
        <div>
          <h1 className="text-[15px] font-semibold leading-tight text-text-primary">
            Training Copilot
          </h1>
          <p className="text-[11px] text-text-muted">AI sports analytics</p>
        </div>
      </div>

      {/* Primary navigation */}
      <nav className="flex flex-col gap-1">
        {nav.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `fd-nav ${isActive ? "fd-nav-active" : ""}`}
          >
            <Icon size={18} strokeWidth={2} />
            {label}
          </NavLink>
        ))}
      </nav>

      <hr className="border-border" />

      {/* Real-time service status */}
      <div>
        <h3 className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          Service status
        </h3>
        <StatusDots />
      </div>

      <hr className="border-border" />

      {/* Secondary controls */}
      <div className="px-1">
        <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          Sport filter
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
        <button onClick={refresh} className="fd-btn-secondary mt-3 flex w-full items-center justify-center gap-2 text-sm">
          <RefreshCw size={15} strokeWidth={2} />
          Refresh data
        </button>
      </div>

      <div className="mt-auto px-1 text-[11px] text-text-muted">Training Copilot · AISS2 Team 6</div>
    </aside>
  );
}
