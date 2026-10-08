// The one per-candidate row behind every surface that lists, plots or selects candidates. Only
// `lib/derivations/round-candidates.ts` merges origin, closed and in-flight rows into it.

import type { AbilityReading, ArmOutcome, LineageNode, VerifyReading } from "@/lib/api/types";

export type CandidateSource = "history" | "inflight";

export type ThetaCaveat = NonNullable<AbilityReading["caveat"]>;

export interface CandidateRow {
  key: string;
  round: number;
  idx: number;
  // The lineage id on a closed row; positional (`liveCandidateId`) on one still in flight.
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
  referenceLift: number | null;
  referenceLiftCiLo: number | null;
  referenceLiftCiHi: number | null;
  is_selected: boolean;
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
  referenceAccuracy: number | null;
  referenceComposite: number | null;
  // How the arm's walk ended; `null` until it is decided. While `invalid`, `accuracy`/`composite`
  // are `INVALID_SCORES`' synthetic 0.0: never render either as a rate.
  outcome: ArmOutcome | null;
}

// ONE array feeds both the bars and the dendrogram beneath them, so they cannot disagree.
export interface CandidateView extends CandidateRow {
  // Served (`reference_lift_side`): which side of 0 the lift interval sits on. `null` where the
  // candidate carries no interval.
  referenceLiftSide: LineageNode["reference_lift_side"];
  // Served (`panel_cut`): stopped short of its round's panel, by its own budget or an eliminator.
  panelCut: boolean;
  // `null` with no `score:` lens, or where the candidate is unscorable under it.
  lensValue: number | null;
  compositeRank: number | null;
  lensRank: number | null;
  // `false` must render as a BLANK, never as a 0.
  started: boolean;
  // Never inferred from the round closing: the election decides the adapters' whole pass
  // earlier, so only this may explain an absent crown.
  electionPending: boolean;
  // Served (`LineageNode.crown`): how a selected candidate advanced. `null` on every other bar,
  // and on a selected one until its round closes.
  crown: LineageNode["crown"];
  // This candidate's last `verify`, as served: the level on cells its rounds never bought.
  verify?: VerifyReading;
  // This candidate read on the held-out bench set. `rows` is set only while its pass is still
  // in flight, when `accuracy` is the running one over `scored` rows and there is no band yet.
  bench?: {
    accuracy: number | null;
    ciLo: number | null;
    ciHi: number | null;
    scored: number;
    rows?: number;
  };
  // `null` unless the candidate answered the WHOLE basis; unlike `accuracy`, it is read on one
  // shared set of cells.
  overlapAccuracy: number | null;
  overlapN: number | null;
}
