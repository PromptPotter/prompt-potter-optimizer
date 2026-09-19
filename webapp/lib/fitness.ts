// The reader-side twin of `promptpotter/domain/scoring.py::is_hit`.
//
// A measurement carries `fitness` — a graded score in [0,1] under the active scorer —
// and nothing else. `hit` is not served, because it was only ever `fitness >= 1.0`:
// redundant with the mean beside it on a binary scorer, and constantly false on a
// graded one (a bounded composite, `rr`, `sigmoid` never reach the ceiling).
//
// So keep the threshold in ONE place on this side too, and use it only where the UI
// must render a DISCRETE word or class (HIT/MISS). Anything that shades, averages or
// ranks should read `fitness` directly — re-imposing the threshold there throws away
// the gradient that makes a graded scorer readable.
//
// The threshold is NOT the whole grade. The LIVE half of this table never reaches here —
// `live_dashboard/blocks.py::sample_row` serves `status` off a four-arm ladder (ERR → UNSC →
// HIT/MISS) — while `round-samples.ts` re-walks that ladder over raw round-file rows with three,
// so a historical UNSC row reads as a wrong answer. The cure is a served `status`, not a fourth
// arm written here.
export const HIT_THRESHOLD = 1.0;

export function isHit(fitness: number | null | undefined): boolean {
  return typeof fitness === "number" && fitness >= HIT_THRESHOLD;
}

// Does THIS ARM's lift interval clear 0 — a reading of the interval already on screen beside it,
// and the one spelling of it: three files each had their own, so the same interval said "spans
// zero", "spans 0 — not separable" and "could not separate from its parent".
//
// It is NOT the round's verdict and may never stand in for one. That question is decided over the
// whole electable field and is SERVED three-state on `RoundSummary.separable`
// (`l1/score/winner.py::_separability`), where `null` — nothing bracketed — is a third answer this
// boolean has no way to carry.
export function liftSeparates(lo: number, hi: number): boolean {
  return lo > 0 || hi < 0;
}

// What a non-separating interval is CALLED, once — the phrase is the verdict, so a surface
// choosing its own wording is the same drift as choosing its own predicate.
export const NOT_SEPARABLE = "could not separate from its parent";
