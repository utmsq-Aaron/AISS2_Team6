// Full-screen 3D flythrough viewer. Fetches the self-contained MapLibre +
// WebCodecs page from /api/flythrough/<id> (authenticated) and renders it via
// <iframe srcdoc> — same-origin, so the in-page Export button can encode + download
// an MP4 client-side. Style / format / quality are passed as query params; changing
// one reloads the page (the map re-initialises).

import { useEffect, useState } from "react";

import { fetchFlythroughHtml } from "../lib/api";

type Opt = { v: string; l: string };
const MODES: Opt[] = [
  { v: "satellite_3d", l: "Satellite 3D" },
  { v: "dark", l: "Dark" },
];
const ORIENTATIONS: Opt[] = [
  { v: "landscape", l: "Landscape" },
  { v: "portrait", l: "Portrait" },
];
const RESOLUTIONS: Opt[] = [
  { v: "HD", l: "HD" },
  { v: "2K", l: "2K" },
  { v: "4K", l: "4K" },
];

function Segmented({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Opt[];
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="fd-label">{label}</span>
      <div className="flex overflow-hidden rounded-lg border border-border">
        {options.map((o) => (
          <button
            key={o.v}
            onClick={() => onChange(o.v)}
            className={
              "px-2.5 py-1 text-xs font-medium transition-colors " +
              (value === o.v
                ? "bg-accent text-[#0B1219]"
                : "text-text-muted hover:bg-bg-surface hover:text-text-primary")
            }
          >
            {o.l}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function FlythroughModal({
  activityId,
  activityName,
  onClose,
}: {
  activityId: number;
  activityName?: string;
  onClose: () => void;
}) {
  const [mode, setMode] = useState("satellite_3d");
  const [orientation, setOrientation] = useState("landscape");
  const [resolution, setResolution] = useState("2K");
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // (Re)load the page whenever the activity or a render option changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setHtml(null);
    fetchFlythroughHtml(activityId, { mode, orientation, resolution })
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activityId, mode, orientation, resolution]);

  // Esc closes the viewer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-bg-card px-4 py-2.5">
        <span className="font-semibold text-text-primary">
          🎥 {activityName || `Activity ${activityId}`}
        </span>
        <Segmented label="Style" value={mode} onChange={setMode} options={MODES} />
        <Segmented label="Format" value={orientation} onChange={setOrientation} options={ORIENTATIONS} />
        <Segmented label="Quality" value={resolution} onChange={setResolution} options={RESOLUTIONS} />
        <button className="fd-btn-secondary ml-auto" onClick={onClose}>
          ✕ Close
        </button>
      </div>

      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 grid place-items-center text-sm text-text-muted">
            Loading 3D flythrough…
          </div>
        )}
        {error && !loading && (
          <div className="absolute inset-0 grid place-items-center p-6 text-center text-sm text-metric-red">
            {error}
          </div>
        )}
        {html && !error && (
          <iframe
            title="3D Flythrough"
            srcDoc={html}
            className="h-full w-full border-0"
            allow="fullscreen; autoplay"
          />
        )}
      </div>
    </div>
  );
}
