// Always-visible RED button testers press to report a problem during the
// prototype-handout phase. Lives in the Header, between the search box and the
// user-profile pill. Clicking it opens FeedbackModal (which does the real work).

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
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-metric-red/15 text-metric-red transition-colors hover:bg-metric-red/25"
      >
        <AlertCircle size={18} strokeWidth={2} />
      </button>
      {isOpen && <FeedbackModal onClose={() => setIsOpen(false)} />}
    </>
  );
}
