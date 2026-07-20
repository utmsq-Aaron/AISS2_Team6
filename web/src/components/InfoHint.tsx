import { Info } from "lucide-react";
import { useId, type ReactNode } from "react";

// Progressive disclosure: a small ⓘ affordance that reveals deeper detail on
// hover OR keyboard focus, so pages can stay short without losing the depth.
// Keep the `text` to a sentence or two — anything longer belongs in a panel.
export function InfoHint({
  text,
  label = "More info",
  className = "",
}: {
  text: ReactNode;
  label?: string;
  className?: string;
}) {
  const id = useId();
  return (
    <span className={`group relative inline-flex align-middle ${className}`}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-text-faint transition-colors hover:text-text-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
      >
        <Info size={13} strokeWidth={2} />
      </button>
      <span
        role="tooltip"
        id={id}
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-[15rem] -translate-x-1/2 rounded-lg border border-border bg-bg-card px-2.5 py-1.5 text-[11.5px] leading-snug text-text-muted opacity-0 shadow-card transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}
