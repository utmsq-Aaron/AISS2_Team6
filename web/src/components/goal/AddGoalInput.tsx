// Goal builder — a guided, widget-driven way to set a goal with almost no typing.
// Goals are stored as freeform text (core.goal_store), so this composes a clean
// sentence from the chosen tab + sport + sliders/presets and submits it as text.
// Same onAdd(text, sport?) contract, so every call site picks it up unchanged.
//
// Scope: swimming, cycling, running only — each with realistic, sport-specific
// distances and the right metric (run pace min/km, bike speed km/h, swim min/100 m).

import { Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { ACCENT } from "../../theme/tokens";

type GoalType = "race" | "volume" | "frequency";

const TABS: { key: GoalType; label: string }[] = [
  { key: "race", label: "Race" },
  { key: "volume", label: "Volume" },
  { key: "frequency", label: "Frequency" },
];

interface Sport {
  key: string;
  label: string;
  icon: string;
  // race distance presets (value in the sport's native unit; 0 = custom)
  presets: { label: string; v: number }[];
  distUnit: string;          // unit for custom distance + composed text
  custom: [number, number, number]; // [min, max, step] for the custom slider
  vol: [number, number, number, string]; // [min, max, step, unit] weekly volume
}

const SPORTS: Sport[] = [
  {
    key: "Run", label: "Running", icon: "🏃",
    presets: [
      { label: "5 km", v: 5 }, { label: "10 km", v: 10 },
      { label: "Half marathon", v: 21.1 }, { label: "Marathon", v: 42.2 },
      { label: "Custom", v: 0 },
    ],
    distUnit: "km", custom: [1, 100, 1], vol: [5, 150, 5, "km/week"],
  },
  {
    key: "Ride", label: "Cycling", icon: "🚴",
    presets: [
      { label: "40 km", v: 40 }, { label: "80 km", v: 80 },
      { label: "100 km", v: 100 }, { label: "Gran fondo (160 km)", v: 160 },
      { label: "Custom", v: 0 },
    ],
    distUnit: "km", custom: [10, 300, 5], vol: [20, 500, 10, "km/week"],
  },
  {
    key: "Swim", label: "Swimming", icon: "🏊",
    presets: [
      { label: "750 m", v: 750 }, { label: "1500 m", v: 1500 },
      { label: "1900 m", v: 1900 }, { label: "3800 m", v: 3800 },
      { label: "Custom", v: 0 },
    ],
    distUnit: "m", custom: [100, 5000, 50], vol: [1, 30, 1, "km/week"],
  },
];

function fmtDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

export function AddGoalInput({
  onAdd,
  adding,
}: {
  onAdd: (text: string, sport?: string) => void;
  adding?: boolean;
}) {
  const [type, setType] = useState<GoalType>("race");
  const [sportKey, setSportKey] = useState("Run");
  const sport = SPORTS.find((s) => s.key === sportKey)!;

  // race — nothing pre-selected; the user actively picks a distance.
  const [presetIdx, setPresetIdx] = useState<number | null>(null);
  const [customDist, setCustomDist] = useState(sport.custom[0]);
  const [date, setDate] = useState("");
  const [target, setTarget] = useState("");
  // volume / frequency
  const [weekly, setWeekly] = useState(sport.vol[0]);
  const [perWeek, setPerWeek] = useState(4);

  const preset = presetIdx == null ? null : sport.presets[presetIdx];

  // Switching sport invalidates the distance/volume ranges — reset them.
  function pickSport(next: Sport) {
    setSportKey(next.key);
    setPresetIdx(null);
    setCustomDist(next.custom[0]);
    setWeekly(next.vol[0]);
  }

  const goalText = useMemo(() => {
    if (type === "race") {
      if (!preset) return "";
      const dist = preset.v === 0 ? `${customDist} ${sport.distUnit}` : preset.label;
      let g = `${sport.label}: ${dist}`;
      if (date) g += ` on ${fmtDate(date)}`;
      if (target.trim()) g += ` under ${target.trim()}`;
      return g;
    }
    if (type === "volume") return `${sport.label}: ${weekly} ${sport.vol[3]}`;
    return `${sport.label}: ${perWeek}× per week`;
  }, [type, sport, preset, customDist, date, target, weekly, perWeek]);

  const valid = goalText.trim().length > 0 && (type !== "race" || (presetIdx != null && !!date));

  function submit() {
    if (!valid || adding) return;
    onAdd(goalText.trim(), sport.key);
    setPresetIdx(null);
    setDate("");
    setTarget("");
  }

  return (
    <div className="fd-card space-y-4 p-4 sm:p-5">
      {/* Goal type — segmented tabs */}
      <div className="inline-flex rounded-lg border border-border bg-bg-surface p-0.5">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setType(t.key)}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
              type === t.key ? "bg-accent text-bg-app" : "text-text-muted hover:text-text-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Sport — chips */}
      <div>
        <div className="fd-label mb-1.5">Sport</div>
        <div className="flex flex-wrap gap-1.5">
          {SPORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => pickSport(s)}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                sportKey === s.key
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-text-muted hover:border-accent/50 hover:text-text-primary"
              }`}
            >
              <span aria-hidden="true">{s.icon}</span> {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Race controls */}
      {type === "race" && (
        <div className="space-y-3">
          <div>
            <div className="fd-label mb-1.5">Distance</div>
            <div className="flex flex-wrap gap-1.5">
              {sport.presets.map((p, i) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => setPresetIdx(i)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                    presetIdx === i
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-text-muted hover:border-accent/50 hover:text-text-primary"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {preset?.v === 0 && (
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min={sport.custom[0]} max={sport.custom[1]} step={sport.custom[2]}
                  value={customDist} onChange={(e) => setCustomDist(Number(e.target.value))}
                  className="flex-1" style={{ accentColor: ACCENT }}
                />
                <span className="w-20 text-sm tabular-nums text-text-primary">{customDist} {sport.distUnit}</span>
              </div>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="fd-label">
              Date
              <input
                type="date" value={date} onChange={(e) => setDate(e.target.value)}
                className="fd-input mt-1 w-full font-normal normal-case tracking-normal text-text-primary"
              />
            </label>
            <label className="fd-label">
              Target time (optional)
              <input
                value={target} onChange={(e) => setTarget(e.target.value)}
                placeholder={sport.key === "Swim" ? "25:00" : "1:45:00"}
                className="fd-input mt-1 w-full font-normal normal-case tracking-normal text-text-primary"
              />
            </label>
          </div>
        </div>
      )}

      {/* Volume */}
      {type === "volume" && (
        <div>
          <div className="fd-label mb-1.5">Weekly volume</div>
          <div className="flex items-center gap-3">
            <input
              type="range" min={sport.vol[0]} max={sport.vol[1]} step={sport.vol[2]}
              value={weekly} onChange={(e) => setWeekly(Number(e.target.value))}
              className="flex-1" style={{ accentColor: ACCENT }}
            />
            <span className="w-24 text-sm tabular-nums text-text-primary">{weekly} {sport.vol[3]}</span>
          </div>
        </div>
      )}

      {/* Frequency */}
      {type === "frequency" && (
        <div>
          <div className="fd-label mb-1.5">Sessions per week</div>
          <div className="flex items-center gap-3">
            <input
              type="range" min={1} max={7} value={perWeek}
              onChange={(e) => setPerWeek(Number(e.target.value))}
              className="flex-1" style={{ accentColor: ACCENT }}
            />
            <span className="w-16 text-sm tabular-nums text-text-primary">{perWeek}× /wk</span>
          </div>
        </div>
      )}

      {/* Live preview + submit */}
      <div className="flex flex-col gap-3 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-w-0 text-sm text-text-muted">
          Goal: <span className="font-medium text-text-primary">{goalText.trim() || "—"}</span>
        </p>
        <button
          type="button"
          className="fd-btn-primary inline-flex shrink-0 items-center justify-center gap-1.5"
          onClick={submit}
          disabled={adding || !valid}
        >
          <Plus size={16} /> {adding ? "Adding…" : "Add goal"}
        </button>
      </div>
    </div>
  );
}
