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

// A round closed before its measurement ran is real history with an empty `candidates[]`,
// and must never be plotted or treated as a completed round.
export function roundHasCandidates(r: RoundSummary): boolean {
  return r.candidates.length > 0;
}

// The rounds that closed WITH measurements, in served order. Not "has a round file": a round
// that closed empty has one too, and `useRoundSource::isRoundUnfiled` asks that instead.
export function measuredRoundNumbers(dash: DashboardSnapshot | null): Set<number> {
  const measured = new Set<number>();
  for (const r of dash?.rounds ?? []) {
    if (roundHasCandidates(r)) measured.add(r.round);
  }
  return measured;
}

// What a dashboard row and a round file's scoreboard row both serve about one scored candidate.
type ServedScore = Pick<
  DashboardCandidate,
  | "accuracy"
  | "composite_fitness"
  | "theta"
  | "theta_se"
  | "theta_caveat"
  | "mean_fitness_ci_lo"
  | "mean_fitness_ci_hi"
  | "reference_accuracy"
  | "reference_composite"
  | "reference_lift"
  | "reference_lift_ci_lo"
  | "reference_lift_ci_hi"
  | "is_selected"
  | "outcome"
>;

// Where the row sits, which only its caller knows: the halves use different id spaces, and a
// scoreboard row carries no label.
interface RowSlot {
  round: number;
  idx: number;
  candidateId: string;
  label: string;
  source: CandidateSource;
  stampsTheta: boolean;
}

// The panel and token account, which the scoreboard does not serve: what it lacks is `null`.
type RowCounts = Pick<
  ElectedRow,
  | "n_samples"
  | "n_expected"
  | "cached_samples"
  | "input_tokens"
  | "output_tokens"
  | "cache_read_tokens"
>;

function countsOf(c: DashboardCandidate): RowCounts {
  return {
    n_samples: c.scored_samples,
    n_expected: c.expected_samples,
    cached_samples: c.cached_samples,
    input_tokens: c.input_tokens,
    output_tokens: c.output_tokens,
    cache_read_tokens: c.cache_read_tokens,
  };
}

// The ONE mapping onto `ElectedRow`: θ and the lift land at the ELECTION, before the round closes,
// so a live row carries them too.
function rowOf(c: ServedScore, slot: RowSlot, counts: RowCounts): ElectedRow {
  const { round, idx, stampsTheta } = slot;
  return {
    key: `R${round}.${idx}`,
    round,
    idx,
    candidate_id: slot.candidateId,
    label: slot.label,
    accuracy: c.accuracy,
    composite: c.composite_fitness,
    // `null` outright where this campaign's selector never fits one — never a blank cell beside
    // a column that does not apply (`RoundSummary.stamps_theta`).
    theta: stampsTheta ? c.theta : null,
    theta_se: stampsTheta ? c.theta_se : null,
    thetaCaveat: stampsTheta ? c.theta_caveat : null,
    meanFitnessCiLo: c.mean_fitness_ci_lo,
    meanFitnessCiHi: c.mean_fitness_ci_hi,
    referenceAccuracy: c.reference_accuracy,
    referenceComposite: c.reference_composite,
    referenceLift: c.reference_lift,
    referenceLiftCiLo: c.reference_lift_ci_lo,
    referenceLiftCiHi: c.reference_lift_ci_hi,
    // `false` may mean nothing is crowned yet, never "lost" — ask `election_held`.
    is_selected: c.is_selected,
    outcome: c.outcome,
    ...counts,
    source: slot.source,
  };
}

// For a cycle this browser holds no stream for. Reads the scoreboard WHOLE, never filled from the
// live half. `total` IS the scored count there.
export function scoreboardRow(
  doc: RoundResult | null,
  candidateId: string,
  label: string,
  round: number,
  idx: number,
): ElectedRow | null {
  const c = doc?.scoreboard.find((r) => r.candidate_id === candidateId);
  if (!doc || !c) return null;
  return rowOf(
    c,
    { round, idx, candidateId, label, source: "history", stampsTheta: doc.stamps_theta },
    {
      n_samples: c.total,
      n_expected: null,
      cached_samples: null,
      input_tokens: null,
      output_tokens: null,
      cache_read_tokens: null,
    },
  );
}

export function roundCandidates(dash: DashboardSnapshot | null): ElectedRow[] {
  const out: ElectedRow[] = [];

  // `rounds[]` is served in round order.
  for (const r of dash?.rounds ?? []) {
    if (!roundHasCandidates(r)) continue;
    // A closed row keys on its LINEAGE id, which the summary always stamps.
    r.candidates.forEach((c, idx) =>
      out.push(
        rowOf(
          c,
          {
            round: r.round,
            idx,
            candidateId: c.candidate_id,
            label: c.label,
            source: "history",
            stampsTheta: r.stamps_theta,
          },
          countsOf(c),
        ),
      ),
    );
  }

  const liveRound = roundOf(dash);
  if (liveRound != null && !measuredRoundNumbers(dash).has(liveRound)) {
    // Positional: a row key, never a join key — live readers join on `label`. `stamps_theta` is
    // campaign-constant (`LiveDashboardState.stamps_theta`), so the live round reads the same
    // flag a closed one would.
    liveCandidates(dash).forEach((c, idx) =>
      out.push(
        rowOf(
          c,
          {
            round: liveRound,
            idx,
            candidateId: liveCandidateId(liveRound, idx),
            label: c.label,
            source: "inflight",
            stampsTheta: dash?.stamps_theta ?? false,
          },
          countsOf(c),
        ),
      ),
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
