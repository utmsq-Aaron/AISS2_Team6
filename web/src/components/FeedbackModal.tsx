// Bug-report modal for testers during the prototype-handout phase. Mirrors the
// overlay shape of FlythroughModal.tsx (fixed inset-0 backdrop + centered card).
// The backend captures the full diagnostic bundle (logs, chats, etc.) itself —
// this only sends the report text plus cheap client-side context.

import { useEffect, useRef, useState } from "react";

import { submitFeedback } from "../lib/api";
import { useChatStore } from "../store/chatStore";

const CONFIRM_MS = 2000;

export default function FeedbackModal({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bundleId, setBundleId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const confirmed = bundleId !== null;

  // Autofocus the textarea on open.
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  // Auto-close a short beat after a successful submit, resetting the form.
  useEffect(() => {
    if (!confirmed) return;
    const t = setTimeout(() => {
      setText("");
      setBundleId(null);
      onClose();
    }, CONFIRM_MS);
    return () => clearTimeout(t);
  }, [confirmed, onClose]);

  const requestClose = () => {
    if (submitting) return; // don't strand an in-flight request
    onClose();
  };

  // Escape closes the modal (unless a submit is in flight).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") requestClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitting]);

  const handleSubmit = async () => {
    const trimmed = text.trim();
    if (!trimmed) {
      setError("Please describe the problem before submitting.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      // One-off read of the active chat id — not a reactive subscription.
      const chatId = useChatStore.getState().activeId;
      const context: Record<string, unknown> = {
        path: window.location.pathname,
        chat_id: chatId ?? null,
        user_agent: navigator.userAgent,
      };
      const res = await submitFeedback(trimmed, context);
      setBundleId(res.bundle_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-modal-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) requestClose();
      }}
    >
      <div className="fd-card w-full max-w-md p-5">
        <h2 id="feedback-modal-title" className="text-base font-semibold text-text-primary">
          Report a problem
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          Tell us what's not working. We'll capture the relevant logs automatically.
        </p>

        {confirmed ? (
          <div className="mt-4 rounded-lg border border-metric-green/40 bg-metric-green/10 px-4 py-3 text-sm text-metric-green">
            Thanks — sent.
            <div className="mt-1 text-xs text-metric-green/70">Reference: {bundleId}</div>
          </div>
        ) : (
          <>
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="What's not working?"
              aria-label="Feedback message"
              rows={5}
              disabled={submitting}
              className="fd-input mt-4 w-full resize-none disabled:cursor-not-allowed disabled:opacity-60"
            />

            {error && (
              <div className="mt-2 rounded-lg border border-metric-red/40 bg-metric-red/10 px-3 py-2 text-sm text-metric-red">
                {error}
              </div>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="fd-btn-secondary"
                onClick={requestClose}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="fd-btn-primary"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? (
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-bg-app/30 border-t-bg-app" />
                    Sending…
                  </span>
                ) : (
                  "Submit"
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
