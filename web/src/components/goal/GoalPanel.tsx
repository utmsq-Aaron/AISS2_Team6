// The generic, agent-authored goal panel. Every goal renders through this one
// component — the CONTENT (headline/status/tiles/progress/chart/note) comes
// entirely from the backend's Panel spec, never hardcoded here. Handles all
// four panel_status states (empty/building/ready/error) plus the goal's
// lifecycle status (active/achieved/archived) — two independent axes, styled
// distinctly: StatusPill/health colouring is driven by panel.status, while
// lifecycle is a small neutral tag + a dimmed card when archived.

import { AlertCircle, ArchiveRestore, Archive as ArchiveIcon, Pencil, RefreshCw, Sparkles } from "lucide-react";

import type { Goal } from "../../lib/api";
import { timeAgoLabel } from "../../lib/goalFormat";
import { Markdown } from "../chat/Markdown";
import { MetricCard } from "../MetricCard";
import { PlotlyChart } from "../PlotlyChart";
import { Spinner } from "../Spinner";
import { GoalProgressRing } from "./GoalProgressRing";
import { StatusPill } from "./StatusPill";

function lifecycleTag(status: Goal["status"]): { label: string } | null {
  if (status === "achieved") return { label: "Achieved" };
  if (status === "archived") return { label: "Archived" };
  return null;
}

export function GoalPanel({
  goal,
  onRefresh,
  onEdit,
  onArchive,
  onRestore,
  refreshing,
}: {
  goal: Goal;
  onRefresh: () => void;
  onEdit: () => void;
  onArchive: () => void;
  onRestore?: () => void;
  refreshing?: boolean;
}) {
  const { panel, panel_status: panelStatus } = goal;
  const isArchived = goal.status === "archived";
  const tag = lifecycleTag(goal.status);
  const rebuilding = panelStatus === "building" && panel != null;

  return (
    <div className={`fd-card flex flex-col p-5 sm:p-6 ${isArchived ? "opacity-60" : ""}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-lg font-semibold text-text-primary" title={goal.text}>
            {goal.text}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {goal.sport && (
              <span className="inline-block whitespace-nowrap rounded-full border border-border bg-bg-surface px-2 py-0.5 text-[0.72rem] font-medium text-text-muted">
                {goal.sport}
              </span>
            )}
            {goal.source === "coach" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[0.72rem] font-medium text-accent">
                <Sparkles size={12} /> Set by coach
              </span>
            )}
            {tag && (
              <span className="inline-block whitespace-nowrap rounded-full border border-border bg-bg-surface px-2 py-0.5 text-[0.72rem] font-medium text-text-muted">
                {tag.label}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Body — depends on panel_status */}
      <div className="relative mt-4 flex-1">
        {rebuilding && (
          <div className="absolute right-0 top-0 inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-bg-card/90 px-2.5 py-1 text-[0.72rem] font-medium text-accent">
            <RefreshCw size={12} className="animate-spin" /> Updating…
          </div>
        )}

        {panel ? (
          <PanelBody panel={panel} />
        ) : panelStatus === "building" ? (
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <Spinner label="Building your panel…" />
          </div>
        ) : panelStatus === "error" ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <AlertCircle size={28} className="text-metric-red" />
            <p className="text-sm text-text-muted">Couldn't build this panel.</p>
            <button type="button" className="fd-btn-secondary text-sm" onClick={onRefresh}>
              Retry
            </button>
          </div>
        ) : (
          <div className="py-8 text-center text-sm text-text-muted">
            Your panel will appear here shortly.
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="mt-4 flex items-center justify-between gap-2 border-t border-border pt-3">
        <span className="text-xs text-text-muted">
          {timeAgoLabel(goal.panel_updated_at ?? panel?.generated_at)}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing || panelStatus === "building"}
            aria-label="Refresh panel"
            title="Refresh panel"
            className="fd-btn-ghost p-1.5 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <RefreshCw size={15} className={refreshing || panelStatus === "building" ? "animate-spin" : ""} />
          </button>
          <button
            type="button"
            onClick={onEdit}
            aria-label="Edit goal"
            title="Edit goal"
            className="fd-btn-ghost p-1.5"
          >
            <Pencil size={15} />
          </button>
          {isArchived ? (
            <button
              type="button"
              onClick={onRestore ?? onArchive}
              aria-label="Restore goal"
              title="Restore goal"
              className="fd-btn-ghost p-1.5"
            >
              <ArchiveRestore size={15} />
            </button>
          ) : (
            <button
              type="button"
              onClick={onArchive}
              aria-label="Archive goal"
              title="Archive goal"
              className="fd-btn-ghost p-1.5"
            >
              <ArchiveIcon size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── The "ready" (or stale-but-rebuilding) content ─────────────────────────────
function PanelBody({ panel }: { panel: NonNullable<Goal["panel"]> }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-base font-semibold text-text-primary">{panel.headline}</h4>
        <StatusPill status={panel.status} />
      </div>

      {panel.progress && (
        <div className="flex items-center gap-4">
          <GoalProgressRing pct={panel.progress.pct} status={panel.status} size={84} />
          <p className="text-sm text-text-muted">{panel.progress.label}</p>
        </div>
      )}

      {panel.tiles.length > 0 && (
        <div className="grid grid-cols-2 gap-3">
          {panel.tiles.map((tile, i) => (
            <MetricCard key={i} label={tile.label} value={tile.value} sub={tile.sub} />
          ))}
        </div>
      )}

      {panel.chart && panel.chart.points.length > 0 && (
        <PlotlyChart
          data={[
            {
              x: panel.chart.points.map((p) => p.x),
              y: panel.chart.points.map((p) => p.y),
              type: panel.chart.kind === "bar" ? "bar" : "scatter",
              mode: panel.chart.kind === "line" ? "lines+markers" : undefined,
            },
          ]}
          layout={panel.chart.y_label ? { yaxis: { title: { text: panel.chart.y_label } } } : {}}
          height={180}
        />
      )}

      {panel.note && <Markdown>{panel.note}</Markdown>}
    </div>
  );
}
