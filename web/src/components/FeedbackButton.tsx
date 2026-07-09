// Always-visible RED floating action pill testers press to report a problem
// during the prototype-handout phase. Fixed to the bottom-right corner of
// every page — mounted once in App.tsx's MainShell, and again inside
// OnboardingWizard (which replaces the shell with its own full-screen overlay)
// so it's reachable during onboarding too. Clicking it opens FeedbackModal
// (which does the real work).

import { AlertCircle } from "lucide-react";
import { useState } from "react";

import FeedbackModal from "./FeedbackModal";

export function FeedbackButton() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        aria-label="Report a problem"
        title="Report a problem"
        className="fixed bottom-20 right-5 z-40 flex items-center gap-2 rounded-full bg-metric-red px-4 py-2.5 text-white shadow-xl transition-colors hover:bg-metric-red/90 md:bottom-5"
      >
        <AlertCircle size={18} strokeWidth={2} />
        <span className="text-sm font-semibold">Report a problem</span>
      </button>
      {isOpen && <FeedbackModal onClose={() => setIsOpen(false)} />}
    </>
  );
}
