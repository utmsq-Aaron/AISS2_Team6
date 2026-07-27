// RED button testers press to report a problem during the prototype-handout
// phase. Two shapes, same FeedbackModal wiring:
//   - "pill"  (default): a fixed floating action pill at the bottom-right —
//     used inside OnboardingWizard, which has no header and an empty
//     bottom-right corner so nothing overlaps it.
//   - "header": an inline red button sized to sit in the app header's right
//     cluster next to the profile pill (App.tsx's MainShell uses this via
//     Header, so the pill no longer overlaps the Chat composer — issue #24).
// Clicking either opens FeedbackModal (which does the real work).

import { AlertCircle } from "lucide-react";
import { useState } from "react";

import FeedbackModal from "./FeedbackModal";

export function FeedbackButton({ variant = "pill" }: { variant?: "pill" | "header" }) {
  const [isOpen, setIsOpen] = useState(false);

  const className =
    variant === "header"
      ? "flex shrink-0 items-center gap-1.5 rounded-lg bg-metric-red px-2.5 py-1.5 text-white transition-colors hover:bg-metric-red/90"
      : "fixed bottom-20 right-5 z-40 flex items-center gap-2 rounded-full bg-metric-red px-4 py-2.5 text-white shadow-xl transition-colors hover:bg-metric-red/90 md:bottom-5";

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        aria-label="Report a problem"
        title="Report a problem"
        className={className}
      >
        <AlertCircle size={variant === "header" ? 16 : 18} strokeWidth={2} />
        <span
          className={
            variant === "header"
              ? "hidden text-sm font-semibold sm:inline"
              : "text-sm font-semibold"
          }
        >
          Report a problem
        </span>
      </button>
      {isOpen && <FeedbackModal onClose={() => setIsOpen(false)} />}
    </>
  );
}
