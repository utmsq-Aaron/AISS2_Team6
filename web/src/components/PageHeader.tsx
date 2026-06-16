import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-xl font-semibold text-text-primary">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-text-muted">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function ComingSoon({ phase }: { phase: string }) {
  return (
    <div className="rounded-card border border-dashed border-border bg-bg-surface/40 px-6 py-10 text-center text-sm text-text-muted">
      Ported in <span className="font-semibold text-text-primary">{phase}</span>.
    </div>
  );
}
