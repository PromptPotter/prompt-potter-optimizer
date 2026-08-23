// Canonical per-candidate row. Single shape backing every dashboard
// surface that lists, plots, or selects candidates — the candidates card's bars,
// dendrogram + forest nodes, RoundSamplesView groups, ScoringInspector target.
//
// Built by `lib/derivations/round-candidates.ts` from the raw
// `DashboardSnapshot`; components never re-derive the merge of
// origin + completed-round candidates + in-flight candidates
// themselves. That divergence is what produced the lineage-vs-fitness
// off-by-one and asymmetric origin handling.
//
// `round = 0` is the origin round (a single candidate, labelled "C0") —
// not a special row, just the first round. Any `(round, idx)` is a candidate.

export type CandidateSource = "history" | "inflight";

export interface CandidateRow {
  // React + dedup key — stable across renders of the same candidate.
  // Shape: "C0" for origin, `R${round}.${idx}` for candidates.
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
  // Difficulty-adjusted Rasch ability (logit scale) + its SE — the subset-invariant
  // metric the round winner is actually elected on. Explains a lower-accuracy winner:
  // it cleared harder samples. `null` for the in-flight round (no election fit yet) and
  // for candidates outside the round's election fit (eliminated / under the coverage floor).
  theta: number | null;
  theta_se: number | null;
  // The CI whisker (served, never recomputed): the normal-CLT mean interval over the
  // candidate's own rows, stamped the moment it finishes scoring — so an IN-FLIGHT row carries
  // it too, and it does not wait for round close. ONE band, one writer, every arm.
  meanFitnessCiLo: number | null;
  meanFitnessCiHi: number | null;
  // The blocked lift over the floor this candidate was JUDGED against, WITH its 95% interval —
  // served, and the only comparable answer to "by how much": under `per_round_resubset` a bare
  // difference of two accuracies is the luckiest draw minus the fullest one. Sharper than the
  // mean band above, because pairing removes the parent's cell-to-cell variation instead of
  // carrying it as noise. An interval spanning 0 means the round could not separate this
  // candidate from its parent — which is why nothing may render the point estimate alone.
  // `null` below two shared cells: an interval from one pair is a fiction.
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
  // `null` where there is no measured panel to describe — a course, or a sliced bar, whose
  // basis this count does not match.
  cached_samples: number | null;
  // Which source the row came from. Lets renderers tag in-flight bars,
  // and lets the derivation layer prove its own dedup discipline.
  source: CandidateSource;
}

// The spine row PLUS the two matched-parent FLOORS — the origin restricted to the cells one
// candidate measured. Only the round document carries them (`ScoreboardRow` /
// `RoundSummaryCandidate`); `/tree` is a genealogy and serves the lift verdict without the
// floors it was taken over, so a row assembled from the tree is not an elected row.
export interface ElectedRow extends CandidateRow {
  // Under elimination a candidate may have run 8 of 20, so its `accuracy` is NOT comparable
  // to the origin's full-set rate; this is the number the promotion gate used. `null` for
  // candidates outside the election fit — nothing matched them.
  matchedParentAccuracy: number | null;
  matchedParentComposite: number | null;
}

// The spine row PLUS the overlays the candidates card paints on it. ONE array
// feeds the bar categories AND the dendrogram nodes beneath them, so the two
// halves of the Sequence view cannot disagree on count, order, or label —
// there is nothing for them to disagree with.
export interface CandidateView extends CandidateRow {
  // Served What-If lens value (the node's own `lens_value`),
  // never recomputed client-side. `null` when no `score:` lens is active or the
  // candidate is unscorable under it.
  whatif: number | null;
  // Served 1-based sibling ranks by `composite` and by `whatif` (the node's own
  // `composite_rank` / `lens_rank`). An ordering IS a score, so these come down the wire —
  // the rank-shift read-out compares two served numbers rather than sorting its own bars.
  // `null` wherever the underlying value is, and on a sliced or course bar.
  compositeRank: number | null;
  whatifRank: number | null;
  // Any sign of activity. `false` = the slot exists but nothing is scored yet,
  // which the chart must render as a BLANK, never as a 0.
  started: boolean;
  // No election has run in this candidate's round YET, so an uncrowned bar has nothing to
  // have lost to. Read off the crowns themselves — a round is undecided while NO bar in it
  // carries one — not off whether the round has closed: `elect_round_winner` decides at the
  // end of scoring, a whole `l1_critique` call earlier, so "still open" and "undecided" are
  // two different facts and only this one may explain an absent crown.
  electionPending: boolean;
  // Latest `verify` diagnostic run whose source label matches this candidate.
  diag?: { accuracy: number; workspaceN: number; samplesAdded: number };
  // This candidate's rate on the cells the whole adopted line has answered — SERVED
  // (`RoundSummary.overlap`), never re-derived here. `null` for every candidate that is not on
  // that line, which is most of them: only C0 and the winners are measured on the shared set.
  // It is NOT a second opinion about `accuracy` above — that one is read on whatever subset its
  // round bought, and the two are only equal by coincidence.
  overlapAccuracy: number | null;
  // The member's OWN denominator on that set (`OverlapMember.total`) — it travels with the rate
  // above, since a percentage over an unnamed count is the reading this series replaces. Equal
  // to the set size unless one of its cells came back unscoreable for this candidate alone.
  overlapN: number | null;
}
