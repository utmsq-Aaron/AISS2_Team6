// Hand-rolled SVG donut ring showing goal progress. Arc is clamped at 100%; the
// track is a muted slate; the fill is coloured by status. `unknown`/null shows a
// grey track with a "—" centre.

import { statusStyle } from "../../lib/goalFormat";
import { BORDER } from "../../theme/tokens";

export function GoalProgressRing({
  pct,
  status,
  size = 132,
  label,
  sublabel,
}: {
  pct: number | null;
  status: string;
  size?: number;
  label?: string;
  sublabel?: string;
}) {
  const stroke = Math.max(8, Math.round(size * 0.09));
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;

  const isUnknown = pct == null || status === "unknown" || !isFinite(pct as number);
  const clamped = isUnknown ? 0 : Math.max(0, Math.min(100, pct as number));
  const { color } = statusStyle(status);
  const arcColor = isUnknown ? BORDER : color;
  const dash = (clamped / 100) * circumference;

  const centerLabel = isUnknown ? "—" : label ?? `${Math.round(clamped)}%`;

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={
        isUnknown ? "Progress unknown" : `${Math.round(clamped)} percent of goal`
      }
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* Track */}
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={BORDER}
          strokeWidth={stroke}
        />
        {/* Progress arc — rotated -90° so it starts at 12 o'clock, sweeps clockwise */}
        {!isUnknown && dash > 0 && (
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={arcColor}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${dash} ${circumference - dash}`}
            transform={`rotate(-90 ${cx} ${cy})`}
            style={{ transition: "stroke-dasharray 0.5s ease" }}
          />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <span
          className="text-2xl font-bold leading-none text-text-primary"
          style={{ color: isUnknown ? undefined : arcColor }}
        >
          {centerLabel}
        </span>
        {sublabel && (
          <span className="mt-1 px-2 text-[11px] leading-tight text-text-muted">
            {sublabel}
          </span>
        )}
      </div>
    </div>
  );
}
