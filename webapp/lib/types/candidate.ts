// Canonical per-candidate row. Single shape backing every dashboard
// surface that lists, plots, or selects candidates — FitnessPanel bars,
// LineageTree stubs, RoundSamplesView groups, ScoringInspector target.
//
// Built by `lib/derivations/round-candidates.ts` from the raw
// `DashboardSnapshot`; components never re-derive the merge of
// origin + completed-round candidates + in-flight candidates
// themselves. That divergence is what produced the lineage-vs-fitness
// off-by-one and asymmetric origin handling.
//
// `round = 0` and `idx = 0` reserved for origin (the C0 row). Any
// other `(round, idx)` is a real L1 candidate.

export type CandidateSource = "origin" | "history" | "inflight";

export interface CandidateRow {
  // React + dedup key — stable across renders of the same candidate.
  // Shape: "C0" for origin, `R${round}.${idx}` for candidates.
  key: string;
  // Round number. 0 for origin.
  round: number;
  // Position within the round. 0 for origin and for the first L1 candidate.
  idx: number;
  // True only for the origin row (round 0, idx 0).
  is_origin: boolean;
  // Stable id used for selection routing. Falls back to `r${round}_${idx}`
  // when the backend round summary hasn't stamped one. Origin uses "origin".
  candidate_id: string;
  // Display label via `candidateLabel(round, idx)` — uniform across surfaces.
  label: string;
  accuracy: number | null;
  composite: number | null;
  evaluators: Record<string, number>;
  is_winner: boolean;
  // Samples scored so far for this candidate; null when unknown.
  n_samples: number | null;
  // Total sample budget for this candidate; null when not yet announced
  // (only meaningful when `< n_samples` is possible mid-round).
  n_expected: number | null;
  // Which source the row came from. Lets renderers tag in-flight bars,
  // and lets the derivation layer prove its own dedup discipline.
  source: CandidateSource;
}
