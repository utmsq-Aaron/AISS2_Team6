export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-6 text-text-muted">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border border-t-accent" />
      {label && <span className="text-sm">{label}</span>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-metric-red/40 bg-metric-red/10 px-4 py-3 text-sm text-metric-red">
      {message}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg-surface px-4 py-6 text-center text-sm text-text-muted">
      {message}
    </div>
  );
}
