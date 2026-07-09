// Activity map panel (selector + RouteMap + selected-activity card + two-step
// delete + 3D flythrough), the recent-activities grid, and the per-activity
// stream analysis. Relocated verbatim from the old Dashboard.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ActivityAnalysis } from "../dashboard/ActivityAnalysis";
import FlythroughModal from "../FlythroughModal";
import { MetricCard } from "../MetricCard";
import { RouteMap, type MarkerSpec, type PolyLineSpec } from "../RouteMap";
import { EmptyState } from "../Spinner";
import { callTool } from "../../lib/api";
import { decodePolyline } from "../../lib/format";
import {
  dayStr,
  paceStr,
  sportOf,
  type Activity,
  type DeleteResult,
} from "../../lib/stravaTypes";
import { ACCENT, activityIcon } from "../../theme/tokens";

function decodeRoute(a: Activity): [number, number][] {
  return decodePolyline(a.map_polyline);
}

export function ActivityMapSection({ activities }: { activities: Activity[] }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [flythroughOpen, setFlythroughOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const deleteMut = useMutation({
    mutationFn: (id: number) =>
      callTool<DeleteResult>("strava__delete_activity", { activity_id: id }),
    onSuccess: (res) => {
      setConfirmDelete(false);
      if (res.success) {
        setSelectedId(null);
        queryClient.invalidateQueries({ queryKey: ["activities"] });
      }
    },
  });

  const selected =
    selectedId != null ? activities.find((a) => a.id === selectedId) ?? null : null;

  // Clear the selection if the selected activity leaves the filtered list.
  useEffect(() => {
    if (selectedId != null && !activities.some((a) => a.id === selectedId)) {
      setSelectedId(null);
    }
  }, [activities, selectedId]);

  return (
    <>
      {flythroughOpen && selected && (
        <FlythroughModal
          activityId={selected.id}
          activityName={selected.name}
          onClose={() => setFlythroughOpen(false)}
        />
      )}

      <h3 className="mb-3 text-lg font-semibold text-text-primary">Activity Map</h3>
      <ActivityMapPanel
        activities={activities}
        selectedId={selectedId}
        selected={selected}
        onSelect={(id) => {
          setSelectedId(id);
          setFlythroughOpen(false);
          setConfirmDelete(false);
        }}
        onFlythrough={() => setFlythroughOpen(true)}
        confirmDelete={confirmDelete}
        onDeleteClick={() => setConfirmDelete(true)}
        onDeleteCancel={() => setConfirmDelete(false)}
        onDeleteConfirm={(id) => deleteMut.mutate(id)}
        deleting={deleteMut.isPending}
        deleteError={deleteMut.data?.success === false ? deleteMut.data.error : undefined}
      />

      {/* Per-activity stream analysis */}
      {selected && (
        <>
          <div className="my-5 h-px bg-border" />
          <ActivityAnalysis activityId={selected.id} />
        </>
      )}

      <div className="my-5 h-px bg-border" />

      {/* Recent activities */}
      <RecentActivities activities={activities} />
    </>
  );
}

// ── Activity map panel (left control column + right map) ──────────────────────
function ActivityMapPanel({
  activities,
  selectedId,
  selected,
  onSelect,
  onFlythrough,
  confirmDelete,
  onDeleteClick,
  onDeleteCancel,
  onDeleteConfirm,
  deleting,
  deleteError,
}: {
  activities: Activity[];
  selectedId: number | null;
  selected: Activity | null;
  onSelect: (id: number | null) => void;
  onFlythrough: () => void;
  confirmDelete: boolean;
  onDeleteClick: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: (id: number) => void;
  deleting: boolean;
  deleteError?: string;
}) {
  const routed = activities.filter((a) => decodeRoute(a).length > 0);

  const { polylines, markers } = useMemo(() => {
    const lines: PolyLineSpec[] = [];
    const marks: MarkerSpec[] = [];
    const n = routed.length;
    routed.forEach((a, i) => {
      const coords = decodeRoute(a);
      if (!coords.length) return;
      const isSel = selectedId === a.id;
      const isDim = selectedId != null && !isSel;
      const weight = isSel ? 5 : 2;
      const opacity = isSel
        ? 0.95
        : isDim
          ? 0.1
          : Math.max(0.25, 1.0 - (i / Math.max(n, 1)) * 0.75);
      lines.push({ coords, color: ACCENT, weight, opacity });
      if (isSel) {
        marks.push({ lat: coords[0][0], lon: coords[0][1], color: "#2ECC71", label: "Start" });
        marks.push({
          lat: coords[coords.length - 1][0],
          lon: coords[coords.length - 1][1],
          color: "#E74C3C",
          label: "Finish",
        });
      }
    });
    return { polylines: lines, markers: marks };
  }, [routed, selectedId]);

  const sortedForSelect = useMemo(
    () =>
      [...activities].sort((a, b) =>
        (b.start_date || "").localeCompare(a.start_date || ""),
      ),
    [activities],
  );

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_3fr]">
      {/* Left control column */}
      <div>
        <select
          className="fd-input w-full"
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All activities</option>
          {sortedForSelect.map((a) => (
            <option key={a.id} value={a.id}>
              {activityIcon(sportOf(a))} {a.name || "?"} ({(a.start_date || "").slice(0, 10)})
            </option>
          ))}
        </select>

        {routed.length === 0 && (
          <p className="mt-3 text-sm text-text-muted">No GPS routes found.</p>
        )}

        {selected ? (
          <SelectedActivityCard
            activity={selected}
            hasRoute={decodeRoute(selected).length > 0}
            onFlythrough={onFlythrough}
            confirmDelete={confirmDelete}
            onDeleteClick={onDeleteClick}
            onDeleteCancel={onDeleteCancel}
            onDeleteConfirm={() => onDeleteConfirm(selected.id)}
            deleting={deleting}
            deleteError={deleteError}
          />
        ) : (
          <p className="mt-3 text-sm text-text-muted">
            <span className="font-semibold text-text-primary">{routed.length}</span> of{" "}
            {activities.length} activities have GPS routes.
          </p>
        )}
      </div>

      {/* Right map */}
      <div>
        {polylines.length > 0 ? (
          <RouteMap polylines={polylines} markers={markers} height={500} />
        ) : (
          <EmptyState message="No GPS route data available." />
        )}
      </div>
    </div>
  );
}

function SelectedActivityCard({
  activity,
  hasRoute,
  onFlythrough,
  confirmDelete,
  onDeleteClick,
  onDeleteCancel,
  onDeleteConfirm,
  deleting,
  deleteError,
}: {
  activity: Activity;
  hasRoute: boolean;
  onFlythrough: () => void;
  confirmDelete: boolean;
  onDeleteClick: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: () => void;
  deleting: boolean;
  deleteError?: string;
}) {
  const sport = activity.type || activity.sport_type || "";
  const distKm = activity.distance_km ?? 0;
  const tMin = Math.round((activity.moving_time_hours ?? 0) * 60);
  const elev = Math.round(activity.elevation_gain_m ?? 0);
  const spd = activity.avg_speed_kmh ?? 0;
  const hr = activity.avg_heart_rate;

  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="font-semibold text-text-primary">
        {activityIcon(sport)} {activity.name || ""}
      </div>
      <p className="text-xs text-text-muted">
        {sport} · {(activity.start_date || "").slice(0, 10)}
      </p>

      <div className="mt-3 space-y-2">
        <MiniMetric label="Distance" value={`${distKm} km`} />
        <MiniMetric
          label="Duration"
          value={tMin >= 60 ? `${Math.floor(tMin / 60)}h ${tMin % 60}min` : `${tMin} min`}
        />
        {sport === "Run" || sport === "Hike" || sport === "Walk" ? (
          <MiniMetric label="Avg Pace" value={paceStr(spd)} />
        ) : spd > 0 ? (
          <MiniMetric label="Avg Speed" value={`${spd.toFixed(1)} km/h`} />
        ) : null}
        <MiniMetric label="Elevation" value={`${elev} m`} />
        {hr != null && <MiniMetric label="Avg HR" value={`${hr.toFixed(0)} bpm`} />}
      </div>

      {hasRoute && (
        <div className="mt-3">
          <button className="fd-btn-primary w-full" onClick={onFlythrough}>
            🎥 3D Flythrough
          </button>
        </div>
      )}

      {/* Delete with two-step confirm */}
      <div className="mt-3">
        {!confirmDelete ? (
          <button className="fd-btn-secondary w-full" onClick={onDeleteClick}>
            🗑️ Delete activity
          </button>
        ) : (
          <div className="rounded-lg border border-metric-amber/40 bg-metric-amber/10 p-3">
            <p className="mb-2 text-sm text-metric-amber">
              Really delete <span className="font-semibold">{activity.name || ""}</span>? This
              cannot be undone.
            </p>
            <div className="flex gap-2">
              <button
                className="fd-btn-primary flex-1"
                disabled={deleting}
                onClick={onDeleteConfirm}
              >
                {deleting ? "Deleting…" : "✓ Yes, delete"}
              </button>
              <button
                className="fd-btn-secondary flex-1"
                disabled={deleting}
                onClick={onDeleteCancel}
              >
                ✗ Cancel
              </button>
            </div>
            {deleteError && <p className="mt-2 text-xs text-metric-red">{deleteError}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
        {label}
      </div>
      <div className="text-base font-bold text-text-primary">{value}</div>
    </div>
  );
}

// ── Recent activities ─────────────────────────────────────────────────────────
function RecentActivities({ activities }: { activities: Activity[] }) {
  if (activities.length === 0) return null;
  const recent = activities.slice(0, 9);
  return (
    <div>
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Recent Activities</h3>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {recent.map((a) => {
          const sport = sportOf(a);
          const d = dayStr(a);
          const dateLabel = d
            ? new Date(d).toLocaleDateString(undefined, {
                day: "2-digit",
                month: "short",
                year: "numeric",
              })
            : "";
          const distKm = a.distance_km ?? 0;
          const tMin = Math.round((a.moving_time_hours ?? 0) * 60);
          const elev = Math.round(a.elevation_gain_m ?? 0);
          const spd = a.avg_speed_kmh ?? 0;
          return (
            <div key={a.id} className="fd-card fd-card-hover p-4">
              <div className="font-semibold text-text-primary">
                {activityIcon(sport)} {a.name}
              </div>
              <p className="text-xs text-text-muted">
                {sport} · {dateLabel}
              </p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <MiniMetric label="Distance" value={`${distKm} km`} />
                <MiniMetric label="Time" value={`${tMin} min`} />
                {elev > 0 && <MiniMetric label="Elevation" value={`${elev} m`} />}
                {elev > 0 && spd > 0 && (
                  <MiniMetric
                    label={
                      sport === "Run" || sport === "Hike" || sport === "Walk"
                        ? "Pace"
                        : "Speed"
                    }
                    value={
                      sport === "Run" || sport === "Hike" || sport === "Walk"
                        ? paceStr(spd)
                        : `${spd} km/h`
                    }
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
