import { useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { useMutation } from "@tanstack/react-query";

import { ActivityCard } from "../components/sync/ActivityCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorBox, Spinner } from "../components/Spinner";
import {
  streamExport,
  syncFetch,
  type ExportResult,
  type ExportStatus,
  type ExportSummary,
  type SyncActivity,
  type SyncFetchResponse,
} from "../lib/syncApi";

// Sync tab — two-stage Garmin → Strava export (port of ui/sync.py).
//   Stage 1 (setup):   pick a date range, click Fetch. Zero API calls until then.
//   Stage 2 (preview): see all activities, select/deselect, then export over SSE.

// ── Preset date-range options (ui/sync.py _PRESETS) ────────────────────────────
const PRESETS: Record<string, number> = {
  "Last 7 days": 7,
  "Last 30 days": 30,
  "Last 90 days": 90,
  "Last 6 months": 182,
  "Last year": 365,
  "All time": 0, // 0 → use 2000-01-01 as start
  "Custom range": -1, // -1 → show date pickers
};
const PRESET_NAMES = Object.keys(PRESETS);

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
}

// ── Live export log line ───────────────────────────────────────────────────────
interface LogLine {
  status: ExportStatus | "info";
  name: string;
  date?: string;
  message?: string;
  url?: string;
}

const STATUS_ICON: Record<ExportStatus, string> = {
  ok: "✅",
  duplicate: "⚠️",
  skipped: "⚠️",
  error: "❌",
};

export function Sync() {
  // ── Stage 1 inputs ──────────────────────────────────────────────────────────
  const today = useMemo(() => isoDate(new Date()), []);
  const [preset, setPreset] = useState<string>("Last 30 days");
  const [customFrom, setCustomFrom] = useState<string>(daysAgo(30));
  const [customTo, setCustomTo] = useState<string>(today);

  // Resolve the active date range from the preset (matches ui/sync.py _render_setup).
  const { startStr, endStr } = useMemo(() => {
    const days = PRESETS[preset];
    if (preset === "Custom range") return { startStr: customFrom, endStr: customTo };
    if (days === 0) return { startStr: "2000-01-01", endStr: today };
    return { startStr: daysAgo(days), endStr: today };
  }, [preset, customFrom, customTo, today]);

  // ── Fetched activities (presence of these = Stage 2) ──────────────────────────
  const [data, setData] = useState<SyncFetchResponse | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // ── Stage 2 filter/search state ───────────────────────────────────────────────
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"all" | "missing">("all");

  // ── Export state ──────────────────────────────────────────────────────────────
  const [exporting, setExporting] = useState(false);
  const [exportTotal, setExportTotal] = useState(0);
  const [exportDone, setExportDone] = useState(0);
  const [currentName, setCurrentName] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<LogLine[]>([]);
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // ── Fetch (Stage 1 → Stage 2) ─────────────────────────────────────────────────
  const fetchMut = useMutation({
    mutationFn: () => syncFetch(startStr, endStr),
    onSuccess: (resp) => {
      setData(resp);
      setSelected(new Set()); // default: nothing selected — user picks manually
      setSearch("");
      setView("all");
      resetExportState();
    },
  });

  function resetExportState() {
    setExporting(false);
    setExportTotal(0);
    setExportDone(0);
    setCurrentName(null);
    setLogLines([]);
    setSummary(null);
    setExportError(null);
  }

  function changeRange() {
    abortRef.current?.();
    abortRef.current = null;
    setData(null);
    setSelected(new Set());
    resetExportState();
  }

  // ── Derived lists (ui/sync.py _render_preview) ────────────────────────────────
  const activities = data?.activities ?? [];
  const hasMatches = data?.has_matches ?? false;

  const nInStrava = useMemo(
    () => activities.filter((a) => a.in_strava).length,
    [activities],
  );
  const nMissing = activities.length - nInStrava;

  const q = search.trim().toLowerCase();
  const viewActs = useMemo(() => {
    if (view === "missing" && hasMatches) return activities.filter((a) => !a.in_strava);
    return activities;
  }, [activities, view, hasMatches]);

  const visibleActs = useMemo(() => {
    if (!q) return viewActs;
    return viewActs.filter(
      (a) => a.name.toLowerCase().includes(q) || (a.type || "").toLowerCase().includes(q),
    );
  }, [viewActs, q]);

  const nSel = selected.size;

  // ── Selection helpers ─────────────────────────────────────────────────────────
  function toggle(id: number, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function selectVisible() {
    setSelected((prev) => {
      const next = new Set(prev);
      visibleActs.forEach((a) => next.add(a.id));
      return next;
    });
  }
  function deselectAll() {
    setSelected(new Set());
  }

  // ── Run export (SSE) ──────────────────────────────────────────────────────────
  function runExport() {
    const toExport: SyncActivity[] = activities.filter((a) => selected.has(a.id));
    if (!toExport.length || exporting) return;

    resetExportState();
    setExporting(true);
    setExportTotal(toExport.length);

    const refs = toExport.map((a) => ({ id: a.id, name: a.name, date: a.date }));

    abortRef.current = streamExport(refs, {
      onProgress: (p) => {
        setCurrentName(p.name);
      },
      onResult: (r: ExportResult) => {
        const act = activities.find((a) => a.name === r.name);
        setLogLines((prev) => [
          ...prev,
          { status: r.status, name: r.name, date: act?.date, message: r.message, url: r.url },
        ]);
        setExportDone((d) => d + 1);
        requestAnimationFrame(() =>
          logEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }),
        );
      },
      onSummary: (s) => setSummary(s),
      onError: (msg) => setExportError(msg),
      onDone: () => {
        setExporting(false);
        setCurrentName(null);
        abortRef.current = null;
      },
    });
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div>
      <PageHeader
        title="Garmin → Strava Export"
        subtitle="Download FIT files from Garmin Connect and upload them directly to Strava. Strava deduplicates by file hash — re-uploading an existing activity is safe."
      />

      {data ? (
        // ── Stage 2: preview & export ───────────────────────────────────────────
        <div className="space-y-4">
          {/* Header row */}
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-text-primary">
              {activities.length} activities  ·  {data.start} → {data.end}
            </h3>
            <button className="fd-btn-secondary shrink-0" onClick={changeRange}>
              ← Change range
            </button>
          </div>

          {/* Strava status overview */}
          {hasMatches && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div
                className="rounded-lg px-3 py-2 text-[0.85rem]"
                style={{ background: "#22c55e15", border: "1px solid #22c55e44" }}
              >
                ✅ <strong style={{ color: "#22c55e" }}>{nInStrava}</strong> already on Strava
              </div>
              <div
                className="rounded-lg px-3 py-2 text-[0.85rem]"
                style={{ background: "#3b82f615", border: "1px solid #3b82f644" }}
              >
                ⬆️ <strong style={{ color: "#60a5fa" }}>{nMissing}</strong> not yet on Strava
              </div>
            </div>
          )}

          {/* Search + view-filter row */}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[3fr_1fr_1fr]">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="🔍 Activity name or sport type…"
              className="fd-input w-full"
            />
            <button
              className={view === "all" ? "fd-btn-primary" : "fd-btn-secondary"}
              onClick={() => setView("all")}
            >
              All
            </button>
            <button
              className={view === "missing" ? "fd-btn-primary" : "fd-btn-secondary"}
              onClick={() => setView("missing")}
              disabled={!hasMatches}
              title="Show only activities not yet on Strava"
            >
              Missing only
            </button>
          </div>

          {/* Selection row */}
          <div className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[4fr_1fr_1fr]">
            <span className="text-xs text-text-muted">
              <strong className="text-text-primary">{nSel}</strong> selected  ·  showing{" "}
              <strong className="text-text-primary">{visibleActs.length}</strong> of {activities.length}
            </span>
            <button className="fd-btn-secondary" onClick={selectVisible}>
              Select visible
            </button>
            <button className="fd-btn-secondary" onClick={deselectAll}>
              Deselect all
            </button>
          </div>

          <div className="border-t border-border" />

          {/* Activity cards */}
          <div className="space-y-3">
            {visibleActs.map((act) => (
              <ActivityCard
                key={act.id}
                activity={act}
                selected={selected.has(act.id)}
                onToggle={toggle}
                inStrava={hasMatches ? act.in_strava : null}
              />
            ))}
          </div>

          {visibleActs.length === 0 &&
            (q ? (
              <div className="rounded-lg border border-border bg-bg-surface px-4 py-6 text-center text-sm text-text-muted">
                No activities found matching '{search}'.
              </div>
            ) : view === "missing" ? (
              <div
                className="rounded-lg px-4 py-3 text-sm"
                style={{ background: "#22c55e15", border: "1px solid #22c55e44", color: "#22c55e" }}
              >
                All activities in this range are already on Strava! 🎉
              </div>
            ) : null)}

          <div className="border-t border-border" />

          {/* Export controls */}
          {nSel === 0 ? (
            <div className="rounded-lg border border-border bg-bg-surface px-4 py-3 text-sm text-text-muted">
              Select at least one activity to export.
            </div>
          ) : (
            <button className="fd-btn-primary w-full" onClick={runExport} disabled={exporting}>
              ⬆️ Export {nSel} {nSel === 1 ? "activity" : "activities"} → Strava
            </button>
          )}

          {/* Live progress + result log + summary */}
          {(exporting || logLines.length > 0 || exportError) && (
            <ExportPanel
              exporting={exporting}
              total={exportTotal}
              done={exportDone}
              currentName={currentName}
              logLines={logLines}
              summary={summary}
              error={exportError}
              logEndRef={logEndRef}
            />
          )}
        </div>
      ) : (
        // ── Stage 1: setup ──────────────────────────────────────────────────────
        <div className="max-w-xl space-y-4">
          <div>
            <h3 className="mb-2 text-base font-semibold text-text-primary">Select date range</h3>
            <select
              className="fd-input w-full"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              {PRESET_NAMES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>

          {preset === "Custom range" && (
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-text-muted">From</span>
                <input
                  type="date"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  className="fd-input mt-1 w-full"
                />
              </label>
              <label className="block">
                <span className="text-xs text-text-muted">To</span>
                <input
                  type="date"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  className="fd-input mt-1 w-full"
                />
              </label>
            </div>
          )}

          <p className="text-xs text-text-muted">
            {preset === "All time" ? "All recorded activities" : `${startStr}  →  ${endStr}`}
          </p>

          <button
            className="fd-btn-primary w-full"
            onClick={() => fetchMut.mutate()}
            disabled={fetchMut.isPending}
          >
            {fetchMut.isPending ? "Connecting to Garmin Connect…" : "🔍 Fetch Activities from Garmin"}
          </button>

          {fetchMut.isPending && <Spinner label="Connecting to Garmin Connect…" />}
          {fetchMut.isError && (
            <ErrorBox message={`Garmin connection failed: ${String(fetchMut.error)}`} />
          )}
          {fetchMut.isSuccess && (fetchMut.data?.activities.length ?? 0) === 0 && (
            <div className="rounded-lg border border-border bg-bg-surface px-4 py-3 text-sm text-text-muted">
              No activities found between {startStr} and {endStr}.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Export progress bar + scrolling result log + summary ────────────────────────
function ExportPanel({
  exporting,
  total,
  done,
  currentName,
  logLines,
  summary,
  error,
  logEndRef,
}: {
  exporting: boolean;
  total: number;
  done: number;
  currentName: string | null;
  logLines: LogLine[];
  summary: ExportSummary | null;
  error: string | null;
  logEndRef: RefObject<HTMLDivElement>;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : exporting ? 0 : 100;

  const summaryParts: string[] = [];
  if (summary) {
    if (summary.ok) summaryParts.push(`${summary.ok} uploaded`);
    if (summary.duplicate) summaryParts.push(`${summary.duplicate} already on Strava`);
    if (summary.skipped) summaryParts.push(`${summary.skipped} skipped (no FIT)`);
    if (summary.error) summaryParts.push(`${summary.error} errors`);
  }

  return (
    <div className="fd-card space-y-3 p-4">
      {/* Progress bar */}
      <div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-bg-surface">
          <div
            className="h-full rounded-full bg-accent transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        {exporting && (
          <div className="mt-2 text-xs text-text-muted">
            {currentName ? `⏳ Processing — ${currentName}` : "Connecting…"} ({done}/{total})
          </div>
        )}
      </div>

      {error && <ErrorBox message={error} />}

      {/* Scrolling result log */}
      {logLines.length > 0 && (
        <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-border bg-bg-surface p-3 text-sm">
          {logLines.map((line, i) => {
            const icon = line.status === "info" ? "ℹ️" : STATUS_ICON[line.status];
            return (
              <div key={i} className="text-text-primary">
                <span>{icon} </span>
                <strong>{line.name}</strong>
                {line.date && <span className="text-text-muted"> ({line.date})</span>}
                {line.url ? (
                  <>
                    {" — "}
                    <a
                      href={line.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent hover:underline"
                    >
                      View on Strava
                    </a>
                  </>
                ) : line.message ? (
                  <span className="text-text-muted"> — {line.message}</span>
                ) : null}
              </div>
            );
          })}
          <div ref={logEndRef} />
        </div>
      )}

      {/* Final summary */}
      {summary && !exporting && (
        <>
          <div
            className="rounded-lg px-4 py-3 text-sm"
            style={{ background: "#22c55e15", border: "1px solid #22c55e44", color: "#22c55e" }}
          >
            Done — {summaryParts.join(", ")}.
          </div>
          {summary.ok > 0 && (
            <div className="rounded-lg border border-border bg-bg-surface px-4 py-3 text-xs text-text-muted">
              ℹ️ Activities uploaded — the Dashboard tab caches Strava data for 5 min. Use the 🔄
              Refresh data button in the sidebar to see them immediately.
            </div>
          )}
        </>
      )}
    </div>
  );
}
