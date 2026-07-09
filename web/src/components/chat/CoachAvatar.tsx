// Consistent coach identity — used for the coach chat's assistant bubbles and
// the pinned coach row in the sidebar.

/** The coach's name + emoji identity, shared across the UI. */
export const COACH = { name: "Coach", emoji: "🧭" } as const;

/** A small avatar for the coach. `size` maps loosely to the emoji font size. */
export function CoachAvatar({ size = "md" }: { size?: "sm" | "md" }) {
  const cls = size === "sm" ? "text-base" : "text-xl";
  return (
    <span className={`${cls} leading-none`} role="img" aria-label={COACH.name} title={COACH.name}>
      {COACH.emoji}
    </span>
  );
}
