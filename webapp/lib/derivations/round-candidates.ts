// The LEAF cycle's per-round candidate rows, in display order — sole consumer of
// `liveL1Candidates` / `roundOf` / `dash.rounds[]` for candidate-list purposes, and the
// only list whose rows carry the `source` tag that routes a per-sample read live vs
// historical (`samplesForRow`). Its consumers are the sample-scoped surfaces:
// `ScoringInspector` and `RoundSamplesView`.
//
// **It is not the bars.** The candidates card plots the children of the VIEWED node
// straight off the served tree (`CandidatesCard`), because only the tree can express what
// hangs off a candidate — a fork's contributed attempts, an L4 inner run, a course. This
// module cannot: `dashboard.json` knows one cycle's own rounds and nothing below them.
//
// The two are not a stitch and not a drift: both are projections of the same
// `ScoredCandidate`, through field lists derived from a model rather than hand-copied
// (`RoundSummaryCandidate` here, `LedgerCandidate` on the tree side), so a field added to
// the candidate report reaches both or fails loud. What they answer differs — "this
// cycle's rows, with their samples" vs "what descends from what" — which is why both exist.
//
// Origin is not special: it's round 0 in `dash.rounds[]`, a one-candidate round labelled
// "C0", and flows through the same history loop as every other round.

import { liveCandidateId } from "@/lib/candidate-label";
import { liveCandidates, roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { DashboardCandidate } from "@/lib/api/types";
import type {
  CandidateRow,
  CandidateSource,
  RoundCandidates,
  RoundSummary,
} from "@/lib/types";

// A `rounds[]` row is chartable/selectable only if it carries scored candidates.
// When a round closes mid-L2/L3 — no `l1_score` ever fired — the round-display
// projection materializes a row with an empty `candidates[]`. Such a row is real
// history but has no fitness data, so it must NOT be advertised as a completed
// round, plotted, or fallen back to as `lastCompleted` (else the card
// resolves no bars and the round-scoped surfaces blank/hang on it). The single
// predicate every round-row consumer (`roundCandidates`, `availableRounds`) shares.
export function roundHasCandidates(r: RoundSummary): boolean {
  return r.candidates.length > 0;
}

// `dash.rounds[]` ascending by round (round 0 = origin). The one place this
// sort lives — every surface that walks history in order rides this instead of
// re-spelling `(dash?.rounds ?? []).slice().sort(...)` (the card, the axis
// derivation, the spine below).
export function sortedRounds(dash: DashboardSnapshot | null): RoundSummary[] {
  return (dash?.rounds ?? []).slice().sort((a, b) => a.round - b.round);
}

// The set of round numbers that have *closed into history with real fitness
// data* — present in `dash.rounds[]` AND carrying scored candidates. This is
// the single definition of "this round is no longer live"; every liveness gate
// (the round axis, the in-flight spine branch, the round-file source guard)
// reads it so they cannot disagree. An empty L2/L3-terminal round is NOT here
// (no candidates), so it never masks the in-flight round nor misroutes the
// source guard to a not-yet-written round file.
export function closedRoundNumbers(dash: DashboardSnapshot | null): Set<number> {
  const closed = new Set<number>();
  for (const r of dash?.rounds ?? []) {
    if (roundHasCandidates(r)) closed.add(r.round);
  }
  return closed;
}

// All candidate rows for the dashboard, in display order:
//   1. Completed rounds from `dash.rounds[]`, ascending by round (round 0
//      = origin, labelled "C0"), then by their authored index within the round.
//   2. In-flight current-round candidates, only if the current round
//      isn't already represented in `dash.rounds[]`.
//
// Step 2's guard prevents double-counting: once `round:display` closes
// the round into the summary, the in-flight projection drops out of
// this list at the same tick. Both surfaces apply this rule the same
// way because they both ride this list.
// ONE mapping, both halves. A live row and a closed row are the same served shape
// (`DashboardCandidate`), so there is nothing left to merge — the live arm used to hardcode
// `theta`/`meanFitnessCi*`/`matchedParent*` to null on the claim that all of them are "stamped at
// round close", which is false for the CI: `l1/population.py` stamps it the moment a candidate
// finishes scoring, so every in-flight bar was served a whisker and then told it had none.
//
// `candidateId` is the caller's, because the two halves live in DIFFERENT identity spaces and
// that is the one thing they may not share — see the two call sites below.
function rowOf(
  c: DashboardCandidate,
  round: number,
  idx: number,
  candidateId: string,
  source: CandidateSource,
): CandidateRow {
  return {
    key: `R${round}.${idx}`,
    round,
    idx,
    candidate_id: candidateId,
    // Served, never recomposed: `candidate_label` is the projection's own and display sites
    // read it verbatim.
    label: c.label,
    accuracy: c.accuracy,
    composite: c.composite_fitness,
    theta: c.theta,
    theta_se: c.theta_se,
    meanFitnessCiLo: c.mean_fitness_ci_lo,
    meanFitnessCiHi: c.mean_fitness_ci_hi,
    matchedParentAccuracy: c.matched_parent_accuracy,
    matchedParentComposite: c.matched_parent_composite,
    evaluators: c.evaluators,
    // Served on the base shape, so a LIVE row carries it too: the election runs at the end
    // of scoring, not at round close. `false` means nothing has been crowned yet — never
    // that this candidate lost (`derivations/election.ts::crownState` owns that reading).
    is_winner: c.is_winner,
    n_samples: c.scored_samples,
    n_expected: c.expected_samples,
    cached_samples: c.cached_samples,
    source,
  };
}

export function roundCandidates(dash: DashboardSnapshot | null): CandidateRow[] {
  const out: CandidateRow[] = [];

  for (const r of sortedRounds(dash)) {
    // Skip empty historical entries (L2/L3-terminal rounds) — they carry no
    // fitness data and `closedRoundNumbers` already excludes them, so the
    // in-flight branch below isn't suppressed for that round number.
    if (!roundHasCandidates(r)) continue;
    // A closed row keys on its LINEAGE id — the tree's identity space, which selection and the
    // round file both join on. Positional only where the summary never stamped one, the same
    // rule the lineage applies; without it every surface routing selection had to guard for "".
    r.candidates.forEach((c, i) =>
      out.push(rowOf(c, r.round, i, c.candidate_id || liveCandidateId(r.round, i), "history")),
    );
  }

  const liveRound = roundOf(dash);
  if (liveRound != null && !closedRoundNumbers(dash).has(liveRound)) {
    // A live row keys POSITIONALLY, even once scoring stamps a real id on it. The two live
    // readers — the sample tape (`liveCandidate`) and the inspector (`liveCandidateRow`) — join
    // back through `liveCandidateId`, so a row wearing its lineage id resolves in neither and
    // the tape and the inspector go blank for every candidate that finished before its round
    // closed. Construction and match ride one rule or they ride none.
    liveCandidates(dash).forEach((c, i) =>
      out.push(rowOf(c, liveRound, i, liveCandidateId(liveRound, i), "inflight")),
    );
  }

  return out;
}

// Round-grouped view of an already-computed candidate list. Pure regrouping —
// takes the rows so the caller (`useRoundCandidates`) computes the spine once
// per snapshot and groups the same array, rather than running the full merge
// twice. Round 0 holds the origin row when it exists.
export function groupByRound(rows: CandidateRow[]): RoundCandidates {
  const map: RoundCandidates = new Map();
  for (const row of rows) {
    const bucket = map.get(row.round);
    if (bucket) bucket.push(row);
    else map.set(row.round, [row]);
  }
  return map;
}
