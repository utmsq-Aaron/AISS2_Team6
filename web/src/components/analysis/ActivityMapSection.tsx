// Activity map panel — search + selector, an overview map of the period's routes,
// the selected activity's numbers, its 3D flythrough and its stream analysis.
//
// Restored after the Dashboard rework dropped it. What came back is deliberately
// narrower than the original: the "Recent Activities" tile grid and the two-step
// delete are gone, because "Recent trainings" on the Dashboard now owns the
// recent-sessions view and this panel is for finding and recapping an OLDER
// session — which is also why the search box reads the full history rather than
// the period the rest of the page is scoped to.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ActivityAnalysis } from "../dashboard/ActivityAnalysis";
import FlythroughModal from "../FlythroughModal";
import { RouteMap, type MarkerSpec, type PolyLineSpec } from "../RouteMap";
import { EmptyState } from "../Spinner";
import { callTool } from "../../lib/api";
import { decodePolyline } from "../../lib/format";
import {
  dayStr,
  paceStr,
  sportOf,
  type ActivitiesResult,
  type Activity,
} from "../../lib/stravaTypes";
import { useUiStore } from "../../store/uiStore";
import { ACCENT, MAP_FINISH, MAP_START, activityIcon } from "../../theme/tokens";

/** How far back the search reaches. Matches the ceiling the comparison section
 *  uses, so both share one cached list instead of fetching the history twice. */
const SEARCH_LIMIT = 500;

function optionLabel(a: Activity): string {
  return `${activityIcon(sportOf(a))} ${a.name || "?"} (${dayStr(a)})`;
}

export function ActivityMapSection({ activities }: { activities: Activity[] }) {
  const refreshVersion = useUiStore((s) => s.refreshVersion);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [flythroughOpen, setFlythroughOpen] = useState(false);

  // The full history, for the search box only — the map still shows the period
  // selected in Overview. Same query key and shape as the comparison section's
  // list, so whichever section opens first fills the cache for both.
  const { data: allActs, isLoading: historyLoading } = useQuery({
    queryKey: ["analysis", "all_activities", refreshVersion],
    queryFn: async () => {
      const raw = await callTool<ActivitiesResult | Activity[]>("strava__get_activities", {
        limit: SEARCH_LIMIT,
      });
      if (Array.isArray(raw)) return raw;
      if (raw && !("error" in raw && raw.error)) return raw.activities ?? [];
      return [] as Activity[];
    },
  });

  // Until the history lands, offer what the page already has.
  const searchable = useMemo(() => allActs ?? activities, [allActs, activities]);

  // Resolved against the full list, not the search results: typing a new query
  // to look something else up must not clear what you are currently viewing.
  // Deriving it also means a selection that disappears from the data (a refresh,
  // a shorter history) falls back to "All activities" on its own.
  const selected =
    selectedId != null ? searchable.find((a) => a.id === selectedId) ?? null : null;

  const keyword = search.trim().toLowerCase();
  const matches = useMemo(
    () =>
      keyword
        ? searchable.filter((a) => (a.name ?? "").toLowerCase().includes(keyword))
        : searchable,
    [searchable, keyword],
  );

  const options = useMemo(() => {
    // Keep the current selection in the list even when it doesn't match the
    // search — otherwise the dropdown renders blank while the map still shows
    // that route.
    const withSelection =
      selected && !matches.some((a) => a.id === selected.id) ? [...matches, selected] : matches;
    return [...withSelection].sort((a, b) => dayStr(b).localeCompare(dayStr(a)));
  }, [matches, selected]);

  // Decode once per activity rather than per render — this is the expensive part
  // of the panel, and "All time" can put several hundred tracks on the map.
  // The selected activity is always drawn, even when it predates the period.
  const routes = useMemo(() => {
    const out = new Map<number, [number, number][]>();
    const draw = [...activities];
    if (selected && !draw.some((a) => a.id === selected.id)) draw.push(selected);
    for (const a of draw) {
      const coords = decodePolyline(a.map_polyline);
      if (coords.length) out.set(a.id, coords);
    }
    return out;
  }, [activities, selected]);

  // The resolved selection, not the raw id: an id that no longer exists in the
  // data would otherwise dim every route to 0.1 with nothing highlighted.
  const selId = selected?.id ?? null;

  const { polylines, markers } = useMemo(() => {
    const lines: PolyLineSpec[] = [];
    const marks: MarkerSpec[] = [];
    const ids = [...routes.keys()];
    ids.forEach((id, i) => {
      const coords = routes.get(id)!;
      const isSel = selId === id;
      const isDim = selId != null && !isSel;
      lines.push({
        coords,
        pickId: id,
        color: ACCENT,
        weight: isSel ? 5 : 2,
        opacity: isSel
          ? 0.95
          : isDim
            ? 0.1
            : Math.max(0.25, 1.0 - (i / Math.max(ids.length, 1)) * 0.75),
      });
      if (isSel) {
        marks.push({ lat: coords[0][0], lon: coords[0][1], color: MAP_START, label: "Start" });
        const end = coords[coords.length - 1];
        marks.push({ lat: end[0], lon: end[1], color: MAP_FINISH, label: "Finish" });
      }
    });
    return { polylines: lines, markers: marks };
  }, [routes, selId]);

  // Read off the decoded map rather than decoding again — this is only rendered
  // while nothing is selected, and in that case `routes` holds exactly the
  // period's tracks. Re-filtering here would re-decode every polyline on every
  // keystroke in the search box.
  const routedInPeriod = routes.size;
  // A hit from outside the current period is worth calling out — otherwise the
  // map showing exactly one track looks like the other routes failed to load.
  const outsidePeriod = selected != null && !activities.some((a) => a.id === selected.id);

  return (
    <>
      {flythroughOpen && selected && (
        <FlythroughModal
          activityId={selected.id}
          activityName={selected.name}
          onClose={() => setFlythroughOpen(false)}
        />
      )}

      {/* Controls stack ABOVE the full-width map rather than beside it. As a
          left-hand column they filled about two thirds of the map's height and
          left the rest blank, and the selected activity's summary ended up
          stranded next to a map instead of over it — the same arrangement
          "Recent trainings" already uses: search, then the session line, then
          the map it describes. */}
      <div className="space-y-3">
        <div className="grid items-start gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="map-search" className="fd-label mb-1 block">
              Search activity
            </label>
            <input
              id="map-search"
              className="fd-input w-full"
              placeholder="e.g. 'wandern', 'morning run'"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <p className="mt-1 text-xs text-text-muted">
              {historyLoading
                ? "Loading your full history…"
                : `Searches all ${searchable.length} activities — including ones outside the selected period.`}
            </p>
          </div>

          <div>
            <label htmlFor="map-select" className="fd-label mb-1 block">
              Activity
            </label>
            <select
              id="map-select"
              className="fd-input w-full"
              value={selected?.id ?? ""}
              onChange={(e) => {
                setSelectedId(e.target.value ? Number(e.target.value) : null);
                setFlythroughOpen(false);
              }}
            >
              <option value="">
                {keyword ? `All ${matches.length} matches` : "All activities"}
              </option>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {optionLabel(a)}
                </option>
              ))}
            </select>
            {keyword && matches.length === 0 && (
              <p className="mt-1 text-xs text-text-muted">
                No activities found matching this search.
              </p>
            )}
          </div>
        </div>

        {selected ? (
          <SelectedActivityRow
            activity={selected}
            hasRoute={routes.has(selected.id)}
            outsidePeriod={outsidePeriod}
            onFlythrough={() => setFlythroughOpen(true)}
          />
        ) : (
          <p className="border-t border-border pt-3 text-xs text-text-muted">
            <span className="font-semibold text-text-primary">{routedInPeriod}</span> of{" "}
            {activities.length} activities in this period have GPS routes. Click a track on the
            map to open it.
          </p>
        )}

        {polylines.length > 0 ? (
          <RouteMap
            polylines={polylines}
            markers={markers}
            height={440}
            ariaLabel="Activity route map"
            onLineClick={(id) => {
              setSelectedId(Number(id));
              setFlythroughOpen(false);
            }}
          />
        ) : (
          <EmptyState message="No GPS route data available." />
        )}
      </div>

      {/* Per-activity stream analysis */}
      {selected && (
        <>
          <div className="my-5 h-px bg-border" />
          <ActivityAnalysis
            activityId={selected.id}
            elevationGainM={selected.elevation_gain_m}
          />
        </>
      )}
    </>
  );
}

// The selected activity, in the same visual language as a "Recent trainings"
// row: name and one muted line of figures on the left, the flythrough as a
// right-aligned ghost action, sitting directly above the map. The detailed
// numbers are deliberately absent — the stream analysis below opens with a
// route summary table that lists all of them.
function SelectedActivityRow({
  activity,
  hasRoute,
  outsidePeriod,
  onFlythrough,
}: {
  activity: Activity;
  hasRoute: boolean;
  outsidePeriod: boolean;
  onFlythrough: () => void;
}) {
  const sport = sportOf(activity);
  const day = dayStr(activity);
  const dateLabel = day
    ? new Date(day).toLocaleDateString("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      })
    : "";
  const spd = activity.avg_speed_kmh ?? 0;
  const paced = sport === "Run" || sport === "Hike" || sport === "Walk";
  const bits = [
    activity.distance_km ? `${activity.distance_km} km` : null,
    activity.moving_time_hours ? `${Math.round(activity.moving_time_hours * 60)} min` : null,
    spd > 0 ? (paced ? paceStr(spd) : `${spd.toFixed(1)} km/h`) : null,
    activity.avg_heart_rate ? `${Math.round(activity.avg_heart_rate)} bpm` : null,
    activity.elevation_gain_m ? `${Math.round(activity.elevation_gain_m)} m ↑` : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-text-primary">
          {activityIcon(sport)} {activity.name || ""}
        </div>
        <p className="mt-0.5 text-xs text-text-muted">
          {[dateLabel, ...bits].filter(Boolean).join(" · ")}
        </p>
        {outsidePeriod && (
          <p className="mt-0.5 text-xs text-text-muted">
            Outside the period selected in Overview — shown on its own.
          </p>
        )}
      </div>

      {hasRoute ? (
        <button
          type="button"
          className="fd-btn-ghost shrink-0 text-xs"
          onClick={onFlythrough}
          aria-label={`Watch a 3D flythrough of ${activity.name}`}
        >
          🎥 3D Flythrough
        </button>
      ) : (
        <span className="shrink-0 text-xs text-text-faint" title="No GPS track recorded">
          no GPS track — no flythrough
        </span>
      )}
    </div>
  );
}
