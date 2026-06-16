// Horizontal pill radio — mirrors the st.radio period selectors on Dashboard/Health.
export function PeriodSelector<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex flex-wrap gap-1 rounded-lg border border-border bg-bg-surface p-1">
      {options.map((opt) => (
        <button
          key={opt}
          onClick={() => onChange(opt)}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            value === opt
              ? "bg-accent text-white"
              : "text-text-muted hover:text-text-primary"
          }`}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}
