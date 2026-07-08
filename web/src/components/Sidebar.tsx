import { useQueryClient } from "@tanstack/react-query";
import { Dumbbell, RefreshCw, X } from "lucide-react";
import { NavLink } from "react-router-dom";

import { NAV } from "../nav";
import { SPORT_TYPES, useUiStore } from "../store/uiStore";
import { StatusDots } from "./StatusDots";

// Sidebar — focused on global navigation + real-time service status (design IA),
// with the sport filter and data refresh as secondary controls below. Below `md`
// it is an off-canvas drawer (toggled from the header hamburger); at `md+` it is
// the usual always-visible rail.
export function Sidebar() {
  const qc = useQueryClient();
  const { sportFilter, setSportFilter, bumpRefresh, sidebarOpen, setSidebarOpen } = useUiStore();

  const refresh = () => {
    bumpRefresh();
    qc.invalidateQueries();
  };

  return (
    <>
      {/* Backdrop — mobile only; tap to dismiss the drawer */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-shrink-0 transform flex-col gap-5 overflow-y-auto border-r border-border bg-bg-sidebar px-3 py-5 transition-transform duration-200 md:static md:z-auto md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
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
          {/* Close — mobile only */}
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-bg-surface hover:text-text-primary md:hidden"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </div>

        {/* Primary navigation */}
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setSidebarOpen(false)}
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
    </>
  );
}
