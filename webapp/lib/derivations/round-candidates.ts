// The leaf cycle's candidate rows, the only list carrying the `source` tag `samplesForRow` routes
// on. Not the bars: those plot the served tree, which alone sees what hangs below a candidate.

import { liveCandidateId } from "@/lib/candidate-label";
import { liveCandidates, roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { DashboardCandidate } from "@/lib/api/types";
import type {
  CandidateSource,
  ElectedRow,
  RoundCandidates,
  RoundResult,
  RoundSummary,
} from "@/lib/types";

// A round closed mid-L2/L3 (no `l1_score` fired) is real history with an empty `candidates[]`,
// and must never be plotted or treated as a completed round.
export function roundHasCandidates(r: RoundSummary): boolean {
  return r.candidates.length > 0;
}

export function sortedRounds(dash: DashboardSnapshot | null): RoundSummary[] {
  return (dash?.rounds ?? []).slice().sort((a, b) => a.round - b.round);
}

// The one definition of "no longer live"; every liveness gate reads it so none can disagree.
export function closedRoundNumbers(dash: DashboardSnapshot | null): Set<number> {
  const closed = new Set<number>();
  for (const r of dash?.rounds ?? []) {
    if (roundHasCandidates(r)) closed.add(r.round);
  }
  return closed;
}

// One mapping for both halves: θ and the lift land at the ELECTION, before the round closes, so a
// live row carries them too. `candidateId` is the caller's: the halves use different id spaces.
function rowOf(
  c: DashboardCandidate,
  round: number,
  idx: number,
  candidateId: string,
  source: CandidateSource,
): ElectedRow {
  return {
    key: `R${round}.${idx}`,
    round,
    idx,
    candidate_id: candidateId,
    label: c.label,
    accuracy: c.accuracy,
    composite: c.composite_fitness,
    theta: c.theta,
    theta_se: c.theta_se,
    thetaCaveat: c.theta_caveat,
    meanFitnessCiLo: c.mean_fitness_ci_lo,
    meanFitnessCiHi: c.mean_fitness_ci_hi,
    matchedParentAccuracy: c.matched_parent_accuracy,
    matchedParentComposite: c.matched_parent_composite,
    matchedParentLift: c.matched_parent_lift,
    matchedParentLiftCiLo: c.matched_parent_lift_ci_lo,
    matchedParentLiftCiHi: c.matched_parent_lift_ci_hi,
    evaluators: c.evaluators,
    // `false` may mean nothing is crowned yet, never "lost" — `election.ts::crownState` reads it.
    is_winner: c.is_winner,
    invalid: c.invalid,
    n_samples: c.scored_samples,
    n_expected: c.expected_samples,
    cached_samples: c.cached_samples,
    input_tokens: c.input_tokens,
    output_tokens: c.output_tokens,
    cache_read_tokens: c.cache_read_tokens,
    source,
  };
}

// For a cycle this browser holds no stream for. Reads the scoreboard WHOLE, never filled from the
// live half; what it lacks stays null. `total` IS the scored count there.
export function scoreboardRow(
  doc: RoundResult | null,
  candidateId: string,
  label: string,
  round: number,
  idx: number,
): ElectedRow | null {
  const c = doc?.scoreboard.find((r) => r.candidate_id === candidateId);
  if (!c) return null;
  return {
    key: `R${round}.${idx}`,
    round,
    idx,
    candidate_id: candidateId,
    label,
    accuracy: c.accuracy,
    composite: c.composite_fitness,
    theta: c.theta,
    theta_se: c.theta_se,
    thetaCaveat: c.theta_caveat,
    meanFitnessCiLo: c.mean_fitness_ci_lo,
    meanFitnessCiHi: c.mean_fitness_ci_hi,
    matchedParentAccuracy: c.matched_parent_accuracy,
    matchedParentComposite: c.matched_parent_composite,
    matchedParentLift: c.matched_parent_lift,
    matchedParentLiftCiLo: c.matched_parent_lift_ci_lo,
    matchedParentLiftCiHi: c.matched_parent_lift_ci_hi,
    evaluators: {},
    is_winner: c.is_winner,
    invalid: c.invalid,
    n_samples: c.total,
    n_expected: null,
    cached_samples: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    source: "history",
  };
}

export function roundCandidates(dash: DashboardSnapshot | null): ElectedRow[] {
  const out: ElectedRow[] = [];

  for (const r of sortedRounds(dash)) {
    if (!roundHasCandidates(r)) continue;
    // A closed row keys on its LINEAGE id; positional only where the summary never stamped one.
    r.candidates.forEach((c, i) =>
      out.push(rowOf(c, r.round, i, c.candidate_id || liveCandidateId(r.round, i), "history")),
    );
  }

  const liveRound = roundOf(dash);
  if (liveRound != null && !closedRoundNumbers(dash).has(liveRound)) {
    // Positional: a row key, never a join key — live readers join on `label`.
    liveCandidates(dash).forEach((c, i) =>
      out.push(rowOf(c, liveRound, i, liveCandidateId(liveRound, i), "inflight")),
    );
  }

  return out;
}

export function groupByRound(rows: ElectedRow[]): RoundCandidates {
  const map: RoundCandidates = new Map();
  for (const row of rows) {
    const bucket = map.get(row.round);
    if (bucket) bucket.push(row);
    else map.set(row.round, [row]);
  }
  return map;
}
