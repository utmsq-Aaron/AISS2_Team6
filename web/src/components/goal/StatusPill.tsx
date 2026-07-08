// Small coloured chip reflecting a goal-progress status.

import { statusStyle } from "../../lib/goalFormat";

export function StatusPill({ status }: { status: string | null | undefined }) {
  const { label, color } = statusStyle(status);
  return (
    <span
      className="inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-[0.78rem] font-medium"
      style={{
        background: `${color}22`,
        color,
        border: `1px solid ${color}55`,
      }}
    >
      {label}
    </span>
  );
}
