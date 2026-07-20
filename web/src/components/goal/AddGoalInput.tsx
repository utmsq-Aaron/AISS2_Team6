// Goal builder — a guided, widget-driven way to set a goal with almost no typing.
// Goals are stored as freeform text (core.goal_store), so this composes a clean
// sentence from the chosen tab + sport + sliders/presets and submits it as text.
// Same onAdd(text, sport?) contract as before, so Dashboard / Settings / Onboarding
// all pick it up unchanged.

import { Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { ACCENT } from "../../theme/tokens";

type GoalType = "race" | "volume" | "frequency" | "free";

const TABS: { key: GoalType; label: string }[] = [
  { key: "race", label: "Wettkampf" },
  { key: "volume", label: "Umfang" },
  { key: "frequency", label: "Häufigkeit" },
  { key: "free", label: "Frei" },
];

const SPORTS = [
  { key: "Run", label: "Laufen", icon: "🏃" },
  { key: "Ride", label: "Rad", icon: "🚴" },
  { key: "Swim", label: "Schwimmen", icon: "🏊" },
  { key: "WeightTraining", label: "Kraft", icon: "🏋️" },
  { key: "Hike", label: "Wandern", icon: "🥾" },
];

// Preset race distances; `0` km = custom (reveals a number field).
const RACE_PRESETS: { label: string; km: number }[] = [
  { label: "5 km", km: 5 },
  { label: "10 km", km: 10 },
  { label: "Halbmarathon", km: 21.1 },
  { label: "Marathon", km: 42.2 },
  { label: "Andere", km: 0 },
];

function sportLabel(key: string): string {
  return SPORTS.find((s) => s.key === key)?.label ?? key;
}

function fmtDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "numeric" });
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
  const [sport, setSport] = useState("Run");

  // race — nothing pre-selected; the user actively picks a distance.
  const [presetIdx, setPresetIdx] = useState<number | null>(null);
  const [customKm, setCustomKm] = useState(15);
  const [date, setDate] = useState("");
  const [target, setTarget] = useState("");
  // volume / frequency
  const [weeklyKm, setWeeklyKm] = useState(40);
  const [perWeek, setPerWeek] = useState(4);
  // free
  const [free, setFree] = useState("");

  const preset = presetIdx == null ? null : RACE_PRESETS[presetIdx];
  const raceKm = preset?.km === 0 ? customKm : preset?.km ?? 0;

  const goalText = useMemo(() => {
    const s = sportLabel(sport);
    if (type === "race") {
      if (!preset) return "";
      const dist = preset.km === 0 ? `${customKm} km` : preset.label;
      let g = preset.km === 0 || sport !== "Run" ? `${s}: ${dist}` : dist;
      if (date) g += ` am ${fmtDate(date)}`;
      if (target.trim()) g += ` in unter ${target.trim()}`;
      return g;
    }
    if (type === "volume") return `${s}: ${weeklyKm} km pro Woche`;
    if (type === "frequency") return `${s}: ${perWeek}× pro Woche trainieren`;
    return free.trim();
  }, [type, sport, preset, customKm, raceKm, date, target, weeklyKm, perWeek, free]);

  const valid = goalText.trim().length > 0 && (type !== "race" || (presetIdx != null && !!date));

  function submit() {
    if (!valid || adding) return;
    onAdd(goalText.trim(), sport);
    setPresetIdx(null);
    setDate("");
    setTarget("");
    setFree("");
  }

  return (
    <div className="fd-card space-y-4 p-4 sm:p-5">
      {/* Type — segmented tabs */}
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

      {/* Sport — chips (hidden for freeform) */}
      {type !== "free" && (
        <div>
          <div className="fd-label mb-1.5">Sportart</div>
          <div className="flex flex-wrap gap-1.5">
            {SPORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setSport(s.key)}
                className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  sport === s.key
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border text-text-muted hover:border-accent/50 hover:text-text-primary"
                }`}
              >
                <span aria-hidden="true">{s.icon}</span> {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Type-specific controls */}
      {type === "race" && (
        <div className="space-y-3">
          <div>
            <div className="fd-label mb-1.5">Distanz</div>
            <div className="flex flex-wrap gap-1.5">
              {RACE_PRESETS.map((p, i) => (
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
            {preset?.km === 0 && (
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min={1} max={100} value={customKm}
                  onChange={(e) => setCustomKm(Number(e.target.value))}
                  className="flex-1" style={{ accentColor: ACCENT }}
                />
                <span className="w-16 text-sm tabular-nums text-text-primary">{customKm} km</span>
              </div>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="fd-label">
              Datum
              <input
                type="date" value={date} onChange={(e) => setDate(e.target.value)}
                className="fd-input mt-1 w-full font-normal normal-case tracking-normal text-text-primary"
              />
            </label>
            <label className="fd-label">
              Zielzeit (optional)
              <input
                value={target} onChange={(e) => setTarget(e.target.value)} placeholder="1:45:00"
                className="fd-input mt-1 w-full font-normal normal-case tracking-normal text-text-primary"
              />
            </label>
          </div>
        </div>
      )}

      {type === "volume" && (
        <div>
          <div className="fd-label mb-1.5">Wochenumfang</div>
          <div className="flex items-center gap-3">
            <input
              type="range" min={5} max={150} step={5} value={weeklyKm}
              onChange={(e) => setWeeklyKm(Number(e.target.value))}
              className="flex-1" style={{ accentColor: ACCENT }}
            />
            <span className="w-20 text-sm tabular-nums text-text-primary">{weeklyKm} km/Wo</span>
          </div>
        </div>
      )}

      {type === "frequency" && (
        <div>
          <div className="fd-label mb-1.5">Einheiten pro Woche</div>
          <div className="flex items-center gap-3">
            <input
              type="range" min={1} max={7} value={perWeek}
              onChange={(e) => setPerWeek(Number(e.target.value))}
              className="flex-1" style={{ accentColor: ACCENT }}
            />
            <span className="w-16 text-sm tabular-nums text-text-primary">{perWeek}× /Wo</span>
          </div>
        </div>
      )}

      {type === "free" && (
        <input
          className="fd-input w-full"
          aria-label="Ziel in eigenen Worten"
          placeholder="Ziel in eigenen Worten — z. B. „Open-Water-Pace verbessern“"
          value={free}
          onChange={(e) => setFree(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          disabled={adding}
        />
      )}

      {/* Live preview + submit */}
      <div className="flex flex-col gap-3 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-w-0 text-sm text-text-muted">
          Ziel:{" "}
          <span className="font-medium text-text-primary">
            {goalText.trim() || "—"}
          </span>
        </p>
        <button
          type="button"
          className="fd-btn-primary inline-flex shrink-0 items-center justify-center gap-1.5"
          onClick={submit}
          disabled={adding || !valid}
        >
          <Plus size={16} /> {adding ? "Wird angelegt…" : "Ziel anlegen"}
        </button>
      </div>
    </div>
  );
}
