// Single source of truth for "which candidates exist per round, in
// display order". Sole consumer of `liveL1Candidates` / `roundOf` /
// `dash.rounds[]` for candidate-list purposes — every surface that
// lists, plots, or selects candidates (FitnessPanel, LineageTree,
// RoundSamplesView, RoundTabsStrip's per-tab counts) reads through this
// helper.
//
// Origin is not special: it's round 0 in `dash.rounds[]`, a one-candidate
// round labelled "C0", and flows through the same history loop as every
// other round.
//
// Why centralized: before this module, FitnessPanel and LineageTree
// each ran their own history-vs-inflight switch with subtly different
// guards, and each picked its own label scheme (FitnessPanel honored
// `c.label`, LineageTree hardcoded `R{round}.{idx+1}`). That drift
// produced the operator-visible bug where R1.1 in lineage and C1.1
// in fitness pointed at different candidates. With one derivation,
// the two surfaces cannot disagree.

import { candidateLabel, liveCandidateId } from "@/lib/candidate-label";
import {
  liveL1Candidates,
  roundOf,
  type DashboardSnapshot,
} from "@/lib/poll";
import type { CandidateRow, RoundCandidates, RoundSummary } from "@/lib/types";

// A `rounds[]` row is chartable/selectable only if it carries scored candidates.
// When a round closes mid-L2/L3 — no `l1_score` ever fired — the round-display
// projection materializes a row with an empty `candidates[]`. Such a row is real
// history but has no fitness data, so it must NOT be advertised as a completed
// round, plotted, or fallen back to as `lastCompleted` (else the FitnessPanel
// resolves no bars and the round-scoped surfaces blank/hang on it). The single
// predicate every round-row consumer (`roundCandidates`, `availableRounds`) shares.
export function roundHasCandidates(r: RoundSummary): boolean {
  return r.candidates.length > 0;
}

// `dash.rounds[]` ascending by round (round 0 = origin). The one place this
// sort lives — every surface that walks history in order rides this instead of
// re-spelling `(dash?.rounds ?? []).slice().sort(...)` (FitnessPanel, the axis
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
export function roundCandidates(dash: DashboardSnapshot | null): CandidateRow[] {
  const out: CandidateRow[] = [];

  for (const r of sortedRounds(dash)) {
    // Skip empty historical entries (L2/L3-terminal rounds) — they carry no
    // fitness data and `closedRoundNumbers` already excludes them, so the
    // in-flight L1_SCORE branch below isn't suppressed for that round number.
    if (!roundHasCandidates(r)) continue;
    r.candidates.forEach((c, i) => {
      out.push({
        key: `R${r.round}.${i}`,
        round: r.round,
        idx: i,
        candidate_id: c.candidate_id,
        label: candidateLabel(r.round, i),
        accuracy: c.accuracy,
        composite: c.composite_fitness,
        evaluators: c.evaluators,
        is_winner: c.is_winner,
        n_samples: c.scored_samples,
        n_expected: c.expected_samples,
        source: "history",
      });
    });
  }

  const liveRound = roundOf(dash);
  if (liveRound != null && !closedRoundNumbers(dash).has(liveRound)) {
    for (const c of liveL1Candidates(dash)) {
      const i = Number(c.idx);
      if (!Number.isFinite(i) || i < 0) continue;
      const evaluators = c.stats?.evaluators ?? {};
      // Served verbatim: the projection fills `accuracy` with the running
      // hit-rate over scored-so-far samples until the final figure lands,
      // so this is never null mid-scoring (no client recompute).
      const accuracy =
        typeof c.stats?.accuracy === "number" ? c.stats.accuracy : null;
      const composite =
        typeof c.stats?.composite_fitness === "number"
          ? c.stats.composite_fitness
          : null;
      out.push({
        key: `R${liveRound}.${i}`,
        round: liveRound,
        idx: i,
        candidate_id: liveCandidateId(liveRound, i),
        label: candidateLabel(liveRound, i),
        accuracy,
        composite,
        evaluators,
        is_winner: false,
        n_samples: c.samples?.length ?? null,
        n_expected: null,
        source: "inflight",
      });
    }
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
