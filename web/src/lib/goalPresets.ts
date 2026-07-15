// Presets for the goal-creation UI (issue #25). These are convenience defaults only —
// they never lock down the freeform intent from epic-2:
//   * GOAL_TEMPLATES PREFILL the editable goal text (you can edit or ignore them).
//   * SPORTS drives a dropdown, but the last entry ("Other") reveals a freeform sport
//     input, so any custom sport still works — no regression on "any sport is valid".

/** Common endurance sports for the sport <select>. "Other" is the escape hatch that
 *  reveals a freeform text input, preserving epic-2's any-sport freedom. */
export const SPORTS = [
  "Running",
  "Hiking",
  "Road Bike",
  "Gravel Bike",
  "Mountain Bike",
  "Swimming",
  "Other",
] as const;

export type Sport = (typeof SPORTS)[number];

/** The sentinel that triggers the freeform custom-sport input. */
export const SPORT_OTHER = "Other";

/** Editable starting text for common endurance goals — picking one PREFILLS the
 *  freeform goal input (it does not replace or lock it). The <select> shows a
 *  "Choose a template…" placeholder as its first (empty-value) option. */
export const GOAL_TEMPLATES: string[] = [
  "Sub-40 10K by December",
  "Finish my first marathon",
  "Improve my FTP by 10% in 8 weeks",
  "Run 3× per week consistently",
  "Complete a 100 km gravel ride",
  "Hike 1000 m elevation in one day",
  "Swim 2 km non-stop",
];
