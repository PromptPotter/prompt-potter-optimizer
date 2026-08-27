// The candidates card's bar spine — the served tree's children of the VIEWED node, turned
// into the one row shape the bars, the dendrogram and the scoring-mask panel all read.
//
// Peer of `round-candidates.ts`: that one normalizes ONE cycle's rounds out of `dashboard.json`
// for the sample-scoped surfaces; this one walks the genealogy, the only source that expresses
// what hangs off a candidate.
//
// Every number here is SERVED — the rules below choose WHICH served number a bar may show, and
// a picked sample set reaches only the overlap channel.

import type {
  DashboardCandidate,
  DiagnosticRunRecord,
  LineageNode,
  OverlapMember,
} from "@/lib/api/types";
import type { CandidateView } from "@/lib/types";
import { panelCellLabel } from "./inner-panel";
import { nodeKeyOf } from "./lineage-candidates";

// A course is a run, not a scored row, so the server decorates it with no basis. Children
// strictly alternate, so this is all-or-nothing and the basis never mixes within one chart.
export function barsAreCourses(viewedNode: LineageNode | undefined): boolean {
  return (viewedNode?.children ?? []).some((n) => n.kind === "course");
}

// A fork's attempt is a course under the hood — the ⑂ marks lead there.
export function forkKeysOf(viewedNode: LineageNode | undefined): Set<string> {
  return new Set(
    (viewedNode?.children ?? []).filter((n) => n.course_kind != null).map((n) => nodeKeyOf(n)),
  );
}

// The count is worth painting over a bar only where it is NEWS — under its own budget, or
// shorter than the fullest panel in its round (a PoBB leader-lock cut). Every bar's count stays
// in the tooltip footer, which is the denominator of record. `null` = nothing to say here.
export function partialPanels(views: readonly CandidateView[]): (number | null)[] {
  const fullest = new Map<number, number>();
  for (const v of views) {
    if (v.n_samples == null) continue;
    fullest.set(v.round, Math.max(fullest.get(v.round) ?? 0, v.n_samples));
  }
  return views.map((v) => {
    const n = v.n_samples;
    if (n == null) return null;
    if (v.n_expected != null && n < v.n_expected) return n;
    return n < (fullest.get(v.round) ?? n) ? n : null;
  });
}

// The latest `verify` run for a candidate, in the shape the chart paints.
function diagView(d: DiagnosticRunRecord | undefined): CandidateView["diag"] {
  return d
    ? { accuracy: d.workspace_accuracy, workspaceN: d.workspace_n, samplesAdded: d.samples_added }
    : undefined;
}

export interface CandidateViewsInput {
  viewedNode: LineageNode | undefined;
  // Label → the row `dash.current_round` is scoring now. Keyed by label because a course's OWN
  // candidates keep their minted label, and `dash` is the viewed course's telemetry.
  inflightByLabel: ReadonlyMap<string, DashboardCandidate>;
  // The cells the operator pinned the overlap bars to, or null for the served reading.
  sampleSet: number[] | null;
  // Whether the mask in force re-derives whole from the masked rows — decided off the served
  // evaluator registry (`scoring-mask::subsetExactFor`), not here.
  lensSubsetExact: boolean;
  diagByLabel: ReadonlyMap<string, DiagnosticRunRecord>;
  overlapByCandidate: ReadonlyMap<string, OverlapMember>;
  // Cells the SERVED reading holds — the denominator a member must match to be readable.
  overlapSize: number | null;
}

export function candidateViews({
  viewedNode,
  inflightByLabel,
  sampleSet,
  lensSubsetExact,
  diagByLabel,
  overlapByCandidate,
  overlapSize,
}: CandidateViewsInput): CandidateView[] {
  const pickedSet = sampleSet != null && !barsAreCourses(viewedNode);
  const basis = pickedSet ? (sampleSet?.length ?? null) : overlapSize;
  // ONE half per bar, ALL-OR-NOTHING: the tree, unless it holds no measurement for this
  // candidate yet. The ledger mints a candidate before it measures one and snapshots the score
  // only at completion, so a bar mid-scoring is the one thing the tree cannot answer.
  return (viewedNode?.children ?? []).map<CandidateView>((n, i) => {
    const isCourse = n.kind === "course";
    // A course shows what it reached, else what it started from. A cut that broke before
    // measuring anything has no number and must render blank, never as its origin's.
    const own = isCourse ? (n.best_accuracy ?? n.origin_accuracy) : n.accuracy;
    const live = isCourse ? undefined : inflightByLabel.get(n.label);
    // An INVALID candidate reports `INVALID_SCORES`' synthetic 0.0 and the tree withholds it, so
    // falling back to the live half would put the fabricated number back on the bar.
    const useLive = live != null && !live.invalid && own == null;
    // The chosen half. Every measured number below reads off THIS, so a bar and its whisker
    // can never come from two different polling clocks.
    const m = useLive ? live : n;
    const accuracy = isCourse ? (own ?? null) : (m.accuracy ?? null);
    const label = isCourse ? (n.task ? panelCellLabel(n.task) : n.dataset_name) : n.label;
    // Drawn ONLY where this candidate answered the WHOLE basis: a rate over a shorter
    // denominator sat a different exam, and differencing these bars is what they are for.
    const member = overlapByCandidate.get(n.id);
    const onBasis = pickedSet ? (n.sample_set_n ?? null) : (member?.total ?? null);
    const whole = !isCourse && basis != null && onBasis === basis;
    return {
      key: nodeKeyOf(n),
      round: n.round ?? 0,
      idx: i,
      candidate_id: n.id,
      label,
      accuracy,
      composite: isCourse ? null : (m.composite_fitness ?? null),
      theta: m.theta ?? null,
      theta_se: m.theta_se ?? null,
      // From the same row as the bar above it, whichever half that was.
      meanFitnessCiLo: m.mean_fitness_ci_lo ?? null,
      meanFitnessCiHi: m.mean_fitness_ci_hi ?? null,
      matchedParentLift: isCourse ? null : n.matched_parent_lift,
      matchedParentLiftCiLo: isCourse ? null : n.matched_parent_lift_ci_lo,
      matchedParentLiftCiHi: isCourse ? null : n.matched_parent_lift_ci_hi,
      evaluators: n.evaluators,
      is_winner: m.is_winner ?? false,
      n_samples: m.scored_samples ?? null,
      n_expected: m.expected_samples ?? null,
      cached_samples: m.cached_samples ?? null,
      source: useLive ? "inflight" : "history",
      // The route composes `lens` and `samples` in one read, so a picked set masks this number
      // too — the one channel besides the overlap bars that a pick still moves.
      lensValue: pickedSet && !lensSubsetExact ? null : n.lens_value,
      // Ranks follow their values exactly, or a bar carries a position in an ordering whose
      // number it is not showing.
      compositeRank: isCourse ? null : n.composite_rank,
      lensRank: pickedSet && !lensSubsetExact ? null : n.lens_rank,
      started: accuracy != null,
      // SERVED, never inferred from whether the round has closed: a round that HELD crowned
      // nobody and reads exactly like one still scoring.
      electionPending: !isCourse && !n.election_held,
      diag: diagView(diagByLabel.get(label)),
      overlapAccuracy: whole
        ? pickedSet
          ? (n.sample_set_accuracy ?? null)
          : (member?.accuracy ?? null)
        : null,
      overlapN: whole ? onBasis : null,
    };
  });
}
