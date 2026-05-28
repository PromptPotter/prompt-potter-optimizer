// Single source of truth for "which candidates exist per round, in
// display order". Sole consumer of `liveL1Candidates` / `roundOf` /
// `dash.rounds[]` / `dash.origin` for candidate-list purposes — every
// surface that lists, plots, or selects candidates (FitnessPanel,
// LineageTree, RoundSamplesView, RoundTabsStrip's per-tab counts)
// reads through this helper.
//
// Why centralized: before this module, FitnessPanel and LineageTree
// each ran their own history-vs-inflight switch with subtly different
// guards, and each picked its own label scheme (FitnessPanel honored
// `c.label`, LineageTree hardcoded `R{round}.{idx+1}`). That drift
// produced the operator-visible bug where R1.1 in lineage and C1.1
// in fitness pointed at different candidates. With one derivation,
// the two surfaces cannot disagree.

import { candidateLabel } from "@/lib/candidate-label";
import {
  liveL1Candidates,
  roundOf,
  type DashboardSnapshot,
} from "@/lib/poll";
import { computeAccuracyFromSamples } from "@/lib/sample-line";
import type { CandidateRow } from "@/lib/types/candidate";
import type { RoundCandidates } from "@/lib/types/round";

// Origin id used everywhere that needs a stable selection target for C0.
// Distinct from any `r{round}_{idx}` so a click on the origin stub never
// collides with a candidate id.
export const ORIGIN_CANDIDATE_ID = "origin";

// Reserved key + label for the origin row. Other rows derive theirs
// from `candidateLabel(round, idx)`; origin's stays "C0" to match the
// label `candidateLabel(0, 0)` already returns.
function originRow(dash: DashboardSnapshot | null): CandidateRow | null {
  const origin = dash?.origin;
  // The `> 0` accuracy guard distinguishes "not yet scored" from
  // "scored at 0% accuracy" — without it, the origin bar would
  // render before the scoring run actually finished.
  const accuracy = origin && origin.accuracy > 0 ? origin.accuracy : null;
  const nSamples = origin && origin.samples > 0 ? origin.samples : null;
  // Render the origin row as soon as we know there is anything else
  // to compare it against; otherwise the origin-only landing page
  // would be visually empty.
  const hasAnything =
    accuracy != null ||
    (dash?.rounds?.length ?? 0) > 0 ||
    liveL1Candidates(dash).length > 0;
  if (!hasAnything) return null;
  return {
    key: "C0",
    round: 0,
    idx: 0,
    is_origin: true,
    candidate_id: ORIGIN_CANDIDATE_ID,
    label: candidateLabel(0, 0),
    accuracy,
    composite: null,
    evaluators: {},
    is_winner: false,
    n_samples: nSamples,
    n_expected: null,
    source: "origin",
  };
}

// All candidate rows for the dashboard, in display order:
//   1. Origin (C0)
//   2. Historical rounds from `dash.rounds[]`, ascending by round,
//      then by their authored index within the round.
//   3. In-flight current-round candidates, only if the current round
//      isn't already represented in `dash.rounds[]`.
//
// Step 3's guard prevents double-counting: once `round:display` closes
// the round into the summary, the in-flight projection drops out of
// this list at the same tick. Both surfaces apply this rule the same
// way because they both ride this list.
export function roundCandidates(dash: DashboardSnapshot | null): CandidateRow[] {
  const out: CandidateRow[] = [];
  const origin = originRow(dash);
  if (origin) out.push(origin);

  const historicalRounds = new Set<number>();
  const history = (dash?.rounds ?? []).slice().sort((a, b) => a.round - b.round);
  for (const r of history) {
    // Skip empty historical entries. When a round closes mid-L2/L3 — no
    // l1_score ever fired — the round-display projection materializes a
    // `rounds[]` row with no candidates. Adding it to `historicalRounds`
    // would suppress the in-flight L1_SCORE branch for the same round
    // number, even though there's no historical data to double-count.
    if (r.candidates.length === 0) continue;
    historicalRounds.add(r.round);
    r.candidates.forEach((c, i) => {
      out.push({
        key: `R${r.round}.${i}`,
        round: r.round,
        idx: i,
        is_origin: false,
        // Fallback mirrors LineageTree's prior local id rule so any
        // selection persisted across the d183dcdb cutover still resolves.
        candidate_id: c.candidate_id || `r${r.round}_${i}`,
        label: candidateLabel(r.round, i),
        accuracy: c.accuracy,
        composite: c.composite_fitness,
        evaluators: c.evaluators ?? {},
        is_winner: c.is_winner,
        n_samples: c.scored_samples,
        n_expected: c.expected_samples,
        source: "history",
      });
    });
  }

  const liveRound = roundOf(dash);
  if (liveRound != null && !historicalRounds.has(liveRound)) {
    for (const c of liveL1Candidates(dash)) {
      const i = Number(c.idx);
      if (!Number.isFinite(i) || i < 0) continue;
      const evaluators = c.stats?.evaluators ?? {};
      const accuracy =
        typeof c.stats?.accuracy === "number"
          ? c.stats.accuracy
          : computeAccuracyFromSamples(c.samples);
      const composite =
        typeof c.stats?.composite_fitness === "number"
          ? c.stats.composite_fitness
          : null;
      out.push({
        key: `R${liveRound}.${i}`,
        round: liveRound,
        idx: i,
        is_origin: false,
        candidate_id: `r${liveRound}_${i}`,
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

// Round-grouped view of the same list. Used by surfaces that scope
// their render to a single round (RoundSamplesView, the round-tabs
// strip's per-tab candidate count). Round 0 holds the origin row
// when it exists.
export function roundCandidatesByRound(
  dash: DashboardSnapshot | null,
): RoundCandidates {
  const map: RoundCandidates = new Map();
  for (const row of roundCandidates(dash)) {
    const bucket = map.get(row.round);
    if (bucket) bucket.push(row);
    else map.set(row.round, [row]);
  }
  return map;
}
