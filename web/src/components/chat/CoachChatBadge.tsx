import { Send, Sparkles } from "lucide-react";

// A small identity badge for the pinned Coach chat: a telegram-ish icon when the
// account is Telegram-linked, else a coach sparkle, plus a red unread dot/count.
export function CoachChatBadge({
  source,
  unread = 0,
}: {
  source?: "telegram" | "coach";
  unread?: number;
}) {
  const Icon = source === "telegram" ? Send : Sparkles;
  const label = source === "telegram" ? "Coach (Telegram)" : "Coach";
  return (
    <span className="relative inline-flex shrink-0 items-center" title={label} aria-label={label}>
      <Icon size={14} strokeWidth={2} className="text-accent" />
      {unread > 0 && (
        <span
          className="absolute -right-1.5 -top-1.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-metric-red px-1 text-[9px] font-semibold leading-none text-white"
          aria-label={`${unread} unread`}
        >
          {unread > 9 ? "9+" : unread}
        </span>
      )}
    </span>
  );
}
