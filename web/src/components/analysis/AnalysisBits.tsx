// Small presentational pieces for the Analysis tab — faithful Tailwind ports of
// the custom HTML helpers in ui/analytics.py (_trend_pill, _pct_bar) plus a
// generic collapsible section card.

import type { ReactNode } from "react";

import { C_GREEN, C_RED, C_ORANGE, TEXT_MUTED, TEXT_FAINT } from "../../theme/tokens";

// ── Collapsible section card (clickable header toggles open/closed) ─────────────

export function CollapsibleSection({
  title,
  caption,
  open,
  onToggle,
  children,
}: {
  title: ReactNode;
  caption?: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className="fd-card mb-5 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-4 px-5 py-4 text-left transition-colors hover:bg-bg-surface/50"
      >
        <div>
          <div className="text-base font-semibold text-text-primary">{title}</div>
          {caption && <div className="mt-0.5 text-xs text-text-muted">{caption}</div>}
        </div>
        <span
          aria-hidden="true"
          className={`mt-1 text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>
      {open && <div className="border-t border-border px-5 py-4">{children}</div>}
    </div>
  );
}

// ── Trend pill (mirrors _trend_pill) ────────────────────────────────────────────

const TREND_MAP: Record<string, { color: string; label: string }> = {
  improving: { color: C_GREEN, label: "📈 Improving" },
  declining: { color: C_RED, label: "📉 Declining" },
  stable: { color: TEXT_MUTED, label: "➡️ Stable" },
  "insufficient data": { color: TEXT_FAINT, label: "❓ Insufficient data" },
};

export function TrendPill({ direction }: { direction: string }) {
  const entry = TREND_MAP[direction] ?? { color: TEXT_FAINT, label: direction };
  return (
    <span
      className="inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-[0.78rem]"
      style={{
        background: `${entry.color}22`,
        color: entry.color,
        border: `1px solid ${entry.color}55`,
      }}
    >
      {entry.label}
    </span>
  );
}

// ── Percentile bar (mirrors _pct_bar) ───────────────────────────────────────────

export function PctBar({
  pct,
  value,
  mean,
  unit,
  label,
}: {
  pct: number;
  value: string;
  mean: string;
  unit: string;
  label: string;
}) {
  const clamp = Math.min(Math.max(pct, 0), 100);
  let barCol: string;
  if (clamp >= 75) barCol = C_RED;
  else if (clamp >= 50) barCol = C_ORANGE;
  else if (clamp <= 25) barCol = C_GREEN;
  else barCol = TEXT_MUTED;

  return (
    <div className="my-2">
      <div className="flex justify-between text-[0.82rem] text-text-muted">
        <span>{label}</span>
        <span>
          <strong>{value}</strong> {unit} &nbsp;·&nbsp; avg&nbsp;{mean} {unit}
        </span>
      </div>
      <div className="mt-1 h-[7px] rounded bg-border">
        <div
          className="h-[7px] rounded"
          style={{ background: barCol, width: `${clamp}%` }}
        />
      </div>
      <div className="text-[0.72rem] text-text-faint">harder than {pct}% of baseline</div>
    </div>
  );
}

// ── How-to expander (mirrors st.expander "💡 …") ────────────────────────────────

export function HowTo({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="mb-3 rounded-lg border border-border bg-bg-surface/60 px-3 py-2 text-sm text-text-muted">
      <summary className="cursor-pointer select-none font-medium text-text-primary">
        {title}
      </summary>
      <div className="mt-2 space-y-2 leading-relaxed">{children}</div>
    </details>
  );
}
