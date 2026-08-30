// Canonical per-candidate row — one shape behind every surface that lists, plots or selects
// candidates: the candidates card's bars, dendrogram + forest nodes, RoundSamplesView groups,
// ScoringInspector target.
//
// Built by `lib/derivations/round-candidates.ts` from the raw `DashboardSnapshot`; components
// never re-derive the merge of origin + completed-round + in-flight candidates themselves.
//
// `round = 0` is the origin round (a single candidate, labelled "C0") — not a special row, just
// the first round. Any `(round, idx)` is a candidate.

export type CandidateSource = "history" | "inflight";

export interface CandidateRow {
  // React + dedup key, stable across renders: "C0" for origin, `R${round}.${idx}` otherwise.
  key: string;
  // Round number. 0 is the origin round.
  round: number;
  // Position within the round. 0 for the origin and for the first L1 candidate.
  idx: number;
  // Stable id used for selection routing. Falls back to `r${round}_${idx}`
  // when the backend round summary hasn't stamped one.
  candidate_id: string;
  // Display label via `candidateLabel(round, idx)` — uniform across surfaces.
  label: string;
  accuracy: number | null;
  composite: number | null;
  // Difficulty-adjusted Rasch ability (logit) + its SE — the subset-invariant metric the winner
  // is elected on. Stamped at the ELECTION, so an in-flight round carries it from there rather
  // than from its close; `null` before it, and for candidates outside the fit.
  theta: number | null;
  theta_se: number | null;
  // The CI whisker (served): the normal-CLT mean interval over the candidate's own rows, folded by
  // the scoring gateway on every sample — so it widens with the bar from the first graded cell.
  meanFitnessCiLo: number | null;
  meanFitnessCiHi: number | null;
  // The blocked lift over the floor this candidate was JUDGED against, WITH its interval —
  // served, and the only comparable answer to "by how much". An interval spanning 0 means the
  // round could not separate it from its parent, which is why nothing may render the point
  // estimate alone. `null` below two shared cells: an interval from one pair is a fiction.
  matchedParentLift: number | null;
  matchedParentLiftCiLo: number | null;
  matchedParentLiftCiHi: number | null;
  evaluators: Record<string, number>;
  is_winner: boolean;
  // Samples scored so far for this candidate; null when unknown.
  n_samples: number | null;
  // Total sample budget for this candidate; null when not yet announced
  // (only meaningful when `< n_samples` is possible mid-round).
  n_expected: number | null;
  // Of `n_samples`, how many were replayed from the archive rather than measured (served).
  // `null` on a course, which has no measured panel to describe.
  cached_samples: number | null;
  // Which source the row came from. Lets renderers tag in-flight bars,
  // and lets the derivation layer prove its own dedup discipline.
  source: CandidateSource;
}

// The spine row PLUS the two matched-parent FLOORS — the origin restricted to the cells one
// candidate measured. Only the round document carries them (`ScoreboardRow` /
// `RoundSummaryCandidate`); `/tree` serves the lift verdict without the floors it was taken
// over, so a row assembled from the tree is not an elected row.
export interface ElectedRow extends CandidateRow {
  // Under elimination a candidate may have run 8 of 20, so its `accuracy` is NOT comparable
  // to the origin's full-set rate; this is the number the promotion gate used. `null` for
  // candidates outside the election fit — nothing matched them.
  matchedParentAccuracy: number | null;
  matchedParentComposite: number | null;
}

// The spine row PLUS the overlays the candidates card paints on it. ONE array feeds the bar
// categories AND the dendrogram nodes beneath them, so the two halves cannot disagree on count,
// order or label.
export interface CandidateView extends CandidateRow {
  // The node's own served `lens_value` — this candidate under the active scoring mask. `null`
  // with no `score:` lens, or where the candidate is unscorable under it.
  lensValue: number | null;
  // Served 1-based sibling ranks by `composite` and by `lensValue`. An ordering IS a score, so
  // these come down the wire and the rank-shift read-out compares two served numbers.
  compositeRank: number | null;
  lensRank: number | null;
  // Any sign of activity. `false` = the slot exists but nothing is scored yet,
  // which the chart must render as a BLANK, never as a 0.
  started: boolean;
  // Served (`election_held`), never inferred from whether the round has closed: the election
  // decides at the end of scoring, a whole `l1_critique` call earlier, so "still open" and
  // "undecided" are two different facts and only this one may explain an absent crown.
  electionPending: boolean;
  // Latest `verify` diagnostic run whose source label matches this candidate.
  diag?: { accuracy: number; workspaceN: number; samplesAdded: number };
  // This candidate's rate on the basis in force — SERVED: the adopted line's own cells by
  // default, or the set the operator pinned. `null` unless it answered the WHOLE basis. NOT a
  // second opinion about `accuracy` above, which is read on whatever subset its round bought.
  overlapAccuracy: number | null;
  // The denominator that rate is over — the basis size wherever the bar is drawn, since a
  // partial reading is blanked. A percentage over an unnamed count is what this series replaces.
  overlapN: number | null;
}
