// The goal-creation UI. Goals stay FREEFORM text by design (epic-2) — this just adds
// convenience presets on top (issue #25), never locking the freeform intent:
//   * a Template <select> that PREFILLS the editable goal text (you can still edit it);
//   * a Sport <select> whose "Other" option reveals a freeform custom-sport input, so
//     any sport still works — the "dropdown" without regressing any-sport freedom;
//   * a collapsible, optional structured "target event" (date / name / distance /
//     sport / elevation) submitted alongside the goal (omitted when left empty).
// Every control is labelled (aria-label or htmlFor), per the a11y pattern from #20.

import { CalendarPlus, ChevronDown, Plus } from "lucide-react";
import { useId, useState } from "react";

import type { GoalEvent } from "../../lib/api";
import { GOAL_TEMPLATES, SPORTS, SPORT_OTHER } from "../../lib/goalPresets";

export function AddGoalInput({
  onAdd,
  adding,
}: {
  onAdd: (text: string, sport?: string, event?: GoalEvent | null) => void;
  adding?: boolean;
}) {
  const uid = useId();
  const [text, setText] = useState("");
  const [sportSel, setSportSel] = useState(""); // "" = unset · "Other" = use customSport
  const [customSport, setCustomSport] = useState("");

  // Optional structured target event (collapsed by default).
  const [showEvent, setShowEvent] = useState(false);
  const [evDate, setEvDate] = useState("");
  const [evName, setEvName] = useState("");
  const [evDistance, setEvDistance] = useState("");
  const [evElevation, setEvElevation] = useState("");
  const [evSport, setEvSport] = useState(""); // "" = same as the goal's sport

  const resolvedSport = (sportSel === SPORT_OTHER ? customSport : sportSel).trim();

  /** The event object to submit, or undefined when no core field is filled. Sport
   *  alone doesn't make an event — it's attached only when a date/name/distance/
   *  elevation is present (else the section is considered empty). */
  function buildEvent(): GoalEvent | undefined {
    const date = evDate.trim() || undefined;
    const name = evName.trim() || undefined;
    const dist = evDistance.trim() ? Number(evDistance) : undefined;
    const elev = evElevation.trim() ? Number(evElevation) : undefined;
    const distance_km = dist !== undefined && !Number.isNaN(dist) ? dist : undefined;
    const elevation_gain_m = elev !== undefined && !Number.isNaN(elev) ? elev : undefined;

    const hasCore = Boolean(date || name || distance_km !== undefined || elevation_gain_m !== undefined);
    if (!hasCore) return undefined;

    const sport = (evSport.trim() || resolvedSport) || undefined;
    return { date, name, distance_km, sport, elevation_gain_m };
  }

  function submit() {
    const t = text.trim();
    if (!t || adding) return;
    onAdd(t, resolvedSport || undefined, buildEvent());
    setText("");
    setSportSel("");
    setCustomSport("");
    setEvDate("");
    setEvName("");
    setEvDistance("");
    setEvElevation("");
    setEvSport("");
    setShowEvent(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="fd-card flex flex-col gap-3 p-4">
      {/* Row 1 — template + freeform goal + sport */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex flex-col gap-1 sm:w-48">
          <label htmlFor={`${uid}-template`} className="fd-label">
            Template
          </label>
          <select
            id={`${uid}-template`}
            className="fd-input"
            value=""
            onChange={(e) => {
              if (e.target.value) setText(e.target.value);
            }}
            disabled={adding}
          >
            <option value="">Choose a template…</option>
            {GOAL_TEMPLATES.map((tpl) => (
              <option key={tpl} value={tpl}>
                {tpl}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor={`${uid}-text`} className="fd-label">
            Goal
          </label>
          <input
            id={`${uid}-text`}
            className="fd-input"
            aria-label="New goal"
            placeholder="Add a goal — e.g. 'Sub-40 10K by December'"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={adding}
          />
        </div>

        <div className="flex flex-col gap-1 sm:w-40">
          <label htmlFor={`${uid}-sport`} className="fd-label">
            Sport
          </label>
          <select
            id={`${uid}-sport`}
            className="fd-input"
            value={sportSel}
            onChange={(e) => setSportSel(e.target.value)}
            disabled={adding}
          >
            <option value="">Sport (optional)</option>
            {SPORTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Freeform custom sport — revealed only when "Other" is picked (keeps any-sport freedom) */}
      {sportSel === SPORT_OTHER && (
        <div className="flex flex-col gap-1 sm:max-w-xs">
          <label htmlFor={`${uid}-custom-sport`} className="fd-label">
            Custom sport
          </label>
          <input
            id={`${uid}-custom-sport`}
            className="fd-input"
            aria-label="Custom sport"
            placeholder="e.g. Trail running, Rowing…"
            value={customSport}
            onChange={(e) => setCustomSport(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={adding}
          />
        </div>
      )}

      {/* Collapsible target-event section */}
      <div className="border-t border-border pt-2">
        <button
          type="button"
          className="fd-btn-ghost inline-flex items-center gap-1.5 text-sm"
          onClick={() => setShowEvent((v) => !v)}
          aria-expanded={showEvent}
          aria-controls={`${uid}-event`}
          disabled={adding}
        >
          <CalendarPlus size={15} />
          Add a target event (optional)
          <ChevronDown size={14} className={showEvent ? "rotate-180 transition-transform" : "transition-transform"} />
        </button>

        {showEvent && (
          <div id={`${uid}-event`} className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label htmlFor={`${uid}-ev-name`} className="fd-label">
                Event name
              </label>
              <input
                id={`${uid}-ev-name`}
                className="fd-input"
                placeholder="e.g. Berlin Marathon"
                value={evName}
                onChange={(e) => setEvName(e.target.value)}
                disabled={adding}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor={`${uid}-ev-date`} className="fd-label">
                Date
              </label>
              <input
                id={`${uid}-ev-date`}
                type="date"
                className="fd-input"
                value={evDate}
                onChange={(e) => setEvDate(e.target.value)}
                disabled={adding}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor={`${uid}-ev-distance`} className="fd-label">
                Distance (km)
              </label>
              <input
                id={`${uid}-ev-distance`}
                type="number"
                min="0"
                step="any"
                className="fd-input"
                placeholder="e.g. 42.2"
                value={evDistance}
                onChange={(e) => setEvDistance(e.target.value)}
                disabled={adding}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor={`${uid}-ev-elevation`} className="fd-label">
                Elevation gain (m)
              </label>
              <input
                id={`${uid}-ev-elevation`}
                type="number"
                min="0"
                step="any"
                className="fd-input"
                placeholder="e.g. 350"
                value={evElevation}
                onChange={(e) => setEvElevation(e.target.value)}
                disabled={adding}
              />
            </div>

            <div className="flex flex-col gap-1 sm:col-span-2 sm:max-w-xs">
              <label htmlFor={`${uid}-ev-sport`} className="fd-label">
                Sport type
              </label>
              <select
                id={`${uid}-ev-sport`}
                className="fd-input"
                value={evSport}
                onChange={(e) => setEvSport(e.target.value)}
                disabled={adding}
              >
                <option value="">Same as goal sport</option>
                {SPORTS.filter((s) => s !== SPORT_OTHER).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {/* Submit */}
      <div className="flex justify-end">
        <button
          type="button"
          className="fd-btn-primary inline-flex shrink-0 items-center justify-center gap-1.5"
          onClick={submit}
          disabled={adding || !text.trim()}
        >
          <Plus size={16} /> {adding ? "Adding…" : "Add goal"}
        </button>
      </div>
    </div>
  );
}
