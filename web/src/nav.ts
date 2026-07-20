import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Flag,
  HeartPulse,
  LayoutDashboard,
  MessageSquare,
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
  { to: "/coach", label: "Coach", icon: Flag },
  { to: "/health", label: "Health", icon: HeartPulse },
  { to: "/analysis", label: "Analysis", icon: Activity },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function navLabel(pathname: string): string {
  return NAV.find((n) => pathname.startsWith(n.to))?.label ?? "Dashboard";
}
