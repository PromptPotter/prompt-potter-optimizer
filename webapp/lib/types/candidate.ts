// The one per-candidate row behind every surface that lists, plots or selects candidates. Only
// `lib/derivations/round-candidates.ts` merges origin, closed and in-flight rows into it.

import type { AbilityReading } from "@/lib/api/types";

export type CandidateSource = "history" | "inflight";

export type ThetaCaveat = NonNullable<AbilityReading["caveat"]>;

export interface CandidateRow {
  key: string;
  round: number;
  idx: number;
  // `r${round}_${idx}` when the round summary has not stamped an id yet.
  candidate_id: string;
  label: string;
  accuracy: number | null;
  composite: number | null;
  // Stamped at the ELECTION, not the round's close; `null` before it and outside the fit.
  theta: number | null;
  theta_se: number | null;
  // Only ever `floor_pinned`: the other caveats describe the round's scale and ride
  // `RoundSummary.ability` once.
  thetaCaveat: ThetaCaveat | null;
  meanFitnessCiLo: number | null;
  meanFitnessCiHi: number | null;
  // Never render the point estimate without its interval: one spanning 0 means the round could not
  // separate it from its parent. `null` below two shared cells.
  matchedParentLift: number | null;
  matchedParentLiftCiLo: number | null;
  matchedParentLiftCiHi: number | null;
  evaluators: Record<string, number>;
  is_winner: boolean;
  n_samples: number | null;
  n_expected: number | null;
  // `null` on a course, which has no measured panel.
  cached_samples: number | null;
  source: CandidateSource;
}

// Only the round document carries the matched-parent floors; `/tree` serves the lift without
// them, so a row assembled from the tree is not an elected row.
export interface ElectedRow extends CandidateRow {
  // The BACKEND bucket only, replays excluded (`TokenAccount.from_measured_rows`): never label it
  // plain "cost", since judge and optimizer spend are not in it.
  input_tokens: number | null;
  output_tokens: number | null;
  // `null` = no provider reported a breakdown, which is NOT 0.
  cache_read_tokens: number | null;
  // Under elimination `accuracy` is not comparable to the origin's full-set rate; this is what the
  // promotion gate used. `null` outside the election fit.
  matchedParentAccuracy: number | null;
  matchedParentComposite: number | null;
  // While true, `accuracy`/`composite` are `INVALID_SCORES`' synthetic 0.0: never render either as
  // a rate, since a rejection is not a zero score.
  invalid: boolean;
}

// ONE array feeds both the bars and the dendrogram beneath them, so they cannot disagree.
export interface CandidateView extends CandidateRow {
  // `null` with no `score:` lens, or where the candidate is unscorable under it.
  lensValue: number | null;
  compositeRank: number | null;
  lensRank: number | null;
  // `false` must render as a BLANK, never as a 0.
  started: boolean;
  // Never inferred from the round closing: the election decides a whole `l1_critique` call
  // earlier, so only this may explain an absent crown.
  electionPending: boolean;
  diag?: { accuracy: number; workspaceN: number; samplesAdded: number };
  // `null` unless the candidate answered the WHOLE basis; unlike `accuracy`, it is read on one
  // shared set of cells.
  overlapAccuracy: number | null;
  overlapN: number | null;
}
