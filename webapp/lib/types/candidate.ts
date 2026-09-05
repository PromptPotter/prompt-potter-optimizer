// Canonical per-candidate row — one shape behind every surface that lists, plots or selects
// candidates: the candidates card's bars, dendrogram + forest nodes, MeasurementRun groups,
// ScoringInspector target.
//
// Built by `lib/derivations/round-candidates.ts` from the raw `DashboardSnapshot`; components
// never re-derive the merge of origin + completed-round + in-flight candidates themselves.
//
// `round = 0` is the origin round (a single candidate, labelled "C0") — not a special row, just
// the first round. Any `(round, idx)` is a candidate.

import type { AbilityReading } from "@/lib/api/types";

export type CandidateSource = "history" | "inflight";

// Derived from the wire, never re-listed: the members are a Python `StrEnum` and a hand-written
// union drifts the moment one is added — which is exactly what happened when `floor_pinned` was.
// One declaration, so the copy map and the candidate row cannot disagree about the vocabulary.
export type ThetaCaveat = NonNullable<AbilityReading["caveat"]>;

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
  // Why that θ is NOT this arm's ability, served. Only ever `floor_pinned` — the other three
  // members describe the round's SCALE and ride `RoundSummary.ability`, once, rather than being
  // copied onto every bar.
  thetaCaveat: ThetaCaveat | null;
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
  // What MEASURING this searchpoint consumed — the BACKEND bucket only, folded over its own
  // measured cells with replays excluded (`domain/spend.py::TokenAccount.from_measured_rows`).
  // Judge spend carries no candidate and optimizer spend is per round, so a row labelled plain
  // "cost" here would silently mean one of three things. On `ElectedRow` rather than the base:
  // the tree serves no account, so a bar row could not answer for it.
  input_tokens: number | null;
  output_tokens: number | null;
  // Of `input_tokens`, how many the PROVIDER served off its own prompt-prefix cache. `null` is
  // "no provider reported a breakdown", which is NOT 0 — and null for the Compare host too, whose
  // source is a round document's scoreboard.
  cache_read_tokens: number | null;
  // Under elimination a candidate may have run 8 of 20, so its `accuracy` is NOT comparable
  // to the origin's full-set rate; this is the number the promotion gate used. `null` for
  // candidates outside the election fit — nothing matched them.
  matchedParentAccuracy: number | null;
  matchedParentComposite: number | null;
  // REJECTED by validation before it cost a single sample — so `accuracy` and `composite` beside
  // it are `INVALID_SCORES`' synthetic 0.0, served deliberately so the row is not byte-identical
  // to one that got everything wrong. **Nothing may render either as a rate while this is true**:
  // a rejection and a zero score are different facts, and `0%` spells the wrong one. WHY it was
  // rejected is not here — that is the scoring node's own account (`candidateVerdicts`), read
  // by the panel that shows the round. `/tree` says the same thing a third way, as
  // `LineageNode.status === "invalid"` with a null accuracy.
  invalid: boolean;
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
