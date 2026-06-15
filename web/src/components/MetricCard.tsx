import type { ReactNode } from "react";

// Mirrors st.metric: uppercase muted label, large value, optional delta/sub.
export function MetricCard({
  label,
  value,
  sub,
  delta,
  deltaColor,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: ReactNode;
  deltaColor?: "green" | "red" | "muted";
}) {
  const deltaCls =
    deltaColor === "green"
      ? "text-metric-green"
      : deltaColor === "red"
        ? "text-metric-red"
        : "text-text-muted";
  return (
    <div className="fd-card fd-card-hover px-5 py-4">
      <div className="text-[11px] font-medium uppercase tracking-wide text-text-muted">
        {label}
      </div>
      <div className="mt-1 text-2xl font-bold text-text-primary">{value}</div>
      {delta != null && <div className={`mt-0.5 text-xs ${deltaCls}`}>{delta}</div>}
      {sub != null && <div className="mt-0.5 text-xs text-text-muted">{sub}</div>}
    </div>
  );
}
