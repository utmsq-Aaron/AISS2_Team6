// "Explain" — one tap turns the chart the user is looking at into 2-3 plain
// sentences about THEIR data (no chat round trip). The caller passes a compact
// summary of exactly what the chart currently renders; the server's LLM seam
// (POST /charts/explain) does the wording. Errors stay quiet and local.
import { useMutation } from "@tanstack/react-query";
import { Loader2, Sparkles, X } from "lucide-react";
import { useState } from "react";

import { explainChart } from "../lib/api";

export function ExplainButton({ title, data, className = "" }: {
  title: string;
  data: Record<string, unknown>;
  className?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => explainChart(title, data),
    onSuccess: (r) => setText(r.explanation),
  });

  if (text) {
    return (
      <div className={`mt-2 flex items-start gap-2 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 ${className}`}>
        <Sparkles size={13} className="mt-0.5 shrink-0 text-accent" />
        <p className="text-xs leading-relaxed text-text-primary">{text}</p>
        <button type="button" aria-label="Dismiss explanation" onClick={() => setText(null)}
          className="ml-auto shrink-0 text-text-faint hover:text-text-primary">
          <X size={13} />
        </button>
      </div>
    );
  }

  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <button type="button" onClick={() => m.mutate()} disabled={m.isPending}
        title="Explain what this chart shows about your data"
        className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-surface px-2 py-1 text-[11px] font-semibold text-text-muted transition-colors hover:border-accent hover:text-text-primary disabled:opacity-50">
        {m.isPending ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
        Explain
      </button>
      {m.isError && (
        <span className="text-[11px] text-metric-red">Explanation unavailable right now.</span>
      )}
    </span>
  );
}
