import { ChevronRight, LogOut, Menu, Search, User } from "lucide-react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { NAV, navLabel } from "../nav";
import { useAuthStore } from "../store/authStore";
import { useUiStore } from "../store/uiStore";

// Minimalist header — breadcrumb, quick search (page jump), and user profile.
export function Header() {
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const { user, logout } = useAuthStore();
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const current = navLabel(location.pathname);
  const matches = query
    ? NAV.filter((n) => n.label.toLowerCase().includes(query.toLowerCase()))
    : NAV;

  const go = (to: string) => {
    navigate(to);
    setQuery("");
    setOpen(false);
  };

  return (
    <header className="flex h-14 flex-shrink-0 items-center justify-between gap-3 border-b border-border bg-bg-header px-4 md:gap-4 md:px-6">
      {/* Hamburger (mobile) + breadcrumb */}
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Open menu"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-surface hover:text-text-primary md:hidden"
        >
          <Menu size={20} strokeWidth={2} />
        </button>
        <nav className="flex min-w-0 items-center gap-1.5 text-sm">
          <span className="hidden text-text-muted sm:inline">Training Copilot</span>
          <ChevronRight size={15} className="hidden text-text-muted/60 sm:inline" strokeWidth={2} />
          <span className="truncate font-semibold text-text-primary">{current}</span>
        </nav>
      </div>

      {/* Quick search (jump to a page) — hidden on small screens; the drawer covers nav */}
      <div className="relative hidden w-full max-w-xs md:block">
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
