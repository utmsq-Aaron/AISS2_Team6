import type { LucideIcon } from "lucide-react";
import {
  Activity,
  HeartPulse,
  LayoutDashboard,
  Map,
  MessageSquare,
  RefreshCw,
  Settings as SettingsIcon,
} from "lucide-react";

// Single source of truth for the primary navigation — used by the Sidebar nav,
// the Header breadcrumb, and the quick-search jump.
export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export const NAV: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/health", label: "Health", icon: HeartPulse },
  { to: "/routes", label: "Routes", icon: Map },
  { to: "/analysis", label: "Analysis", icon: Activity },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/sync", label: "Sync", icon: RefreshCw },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function navLabel(pathname: string): string {
  return NAV.find((n) => pathname.startsWith(n.to))?.label ?? "Dashboard";
}
