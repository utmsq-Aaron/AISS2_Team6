import { ChevronRight, LogOut, Search, User } from "lucide-react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { navLabel, visibleNav } from "../nav";
import { useAuthStore } from "../store/authStore";

// Minimalist header — breadcrumb, quick search (page jump), and user profile.
export function Header() {
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const { user, logout, isAdmin } = useAuthStore();
  const nav = visibleNav(isAdmin);
  const current = navLabel(location.pathname);
  const matches = query
    ? nav.filter((n) => n.label.toLowerCase().includes(query.toLowerCase()))
    : nav;

  const go = (to: string) => {
    navigate(to);
    setQuery("");
    setOpen(false);
  };

  return (
    <header className="flex h-14 flex-shrink-0 items-center justify-between gap-4 border-b border-border bg-bg-header px-6">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm">
        <span className="text-text-muted">Training Copilot</span>
        <ChevronRight size={15} className="text-text-muted/60" strokeWidth={2} />
        <span className="font-semibold text-text-primary">{current}</span>
      </nav>

      {/* Quick search (jump to a page) */}
      <div className="relative w-full max-w-xs">
        <Search
          size={15}
          strokeWidth={2}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
        />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches[0]) go(matches[0].to);
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder="Search pages…"
          className="fd-input w-full py-1.5 pl-9 text-sm"
        />
        {open && query && (
          <ul className="absolute z-30 mt-1 w-full overflow-hidden rounded-lg border border-border bg-bg-card shadow-card">
            {matches.length === 0 ? (
              <li className="px-3 py-2 text-sm text-text-muted">No matches</li>
            ) : (
              matches.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <button
                    onMouseDown={() => go(to)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-surface"
                  >
                    <Icon size={15} strokeWidth={2} className="text-text-muted" />
                    {label}
                  </button>
                </li>
              ))
            )}
          </ul>
        )}
      </div>

      {/* User profile + logout */}
      <div className="flex items-center gap-1.5 rounded-lg border border-border bg-bg-surface px-2.5 py-1.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/15 text-accent">
          <User size={14} strokeWidth={2} />
        </span>
        <span className="hidden text-sm text-text-primary sm:inline">{user ?? "Athlete"}</span>
        <button
          onClick={logout}
          title="Sign out"
          className="ml-1 flex h-6 w-6 items-center justify-center rounded-md text-text-muted hover:bg-bg-app hover:text-text-primary"
        >
          <LogOut size={14} strokeWidth={2} />
        </button>
      </div>
    </header>
  );
}
