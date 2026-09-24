// The served tree's children of the VIEWED node as the one row shape the bars, dendrogram and
// mask panel read. Every number is served; a picked sample set reaches only the overlap channel.

import type {
  DashboardCandidate,
  DiagnosticRunRecord,
  LineageNode,
  OverlapMember,
} from "@/lib/api/types";
import type { CandidateView } from "@/lib/types";
import { panelCellLabel } from "./inner-panel";
import { nodeKeyOf, splitRetired } from "./lineage-candidates";

// A course is a run, not a scored row, so the server decorates it with no basis.
export function barsAreCourses(viewedNode: LineageNode | undefined): boolean {
  return (viewedNode?.children ?? []).some((n) => n.kind === "course");
}

export function forkKeysOf(viewedNode: LineageNode | undefined): Set<string> {
  return new Set(
    (viewedNode?.children ?? []).filter((n) => n.course_kind != null).map((n) => nodeKeyOf(n)),
  );
}

// Painted only where it is NEWS — under its own budget, or shorter than its round's fullest
// panel (a PoBB leader-lock cut); the tooltip footer is the denominator of record.
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

function diagView(d: DiagnosticRunRecord | undefined): CandidateView["diag"] {
  return d
    ? { accuracy: d.workspace_accuracy, workspaceN: d.workspace_n, samplesAdded: d.samples_added }
    : undefined;
}

export interface CandidateViewsInput {
  viewedNode: LineageNode | undefined;
  // Keyed by label: a course's own candidates keep their minted label, and `dash` is its telemetry.
  inflightByLabel: ReadonlyMap<string, DashboardCandidate>;
  // null ⇒ the served reading.
  sampleSet: number[] | null;
  // Decided off the served evaluator registry (`scoring-mask::subsetExactFor`), not here.
  lensSubsetExact: boolean;
  diagByLabel: ReadonlyMap<string, DiagnosticRunRecord>;
  overlapByCandidate: ReadonlyMap<string, OverlapMember>;
  // The denominator a member must match to be readable.
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
  // ONE half per bar, all-or-nothing: the tree, unless it has no score yet (the ledger snapshots
  // one only at completion). Live side of each supersede cut only — retired tails double a round.
  return splitRetired(viewedNode?.children ?? []).live.map<CandidateView>((n, i) => {
    const isCourse = n.kind === "course";
    // A cut that broke before measuring anything renders blank, never as its origin's number.
    const own = isCourse ? (n.best_accuracy ?? n.origin_accuracy) : n.accuracy;
    const live = isCourse ? undefined : inflightByLabel.get(n.label);
    // An INVALID candidate reports `INVALID_SCORES`' synthetic 0.0 and the tree withholds it, so
    // falling back to the live half would put the fabricated number back on the bar.
    const useLive = live != null && !live.invalid && own == null;
    // Every measured number reads off THIS half, so a bar and its whisker share one polling clock.
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
      thetaCaveat: m.theta_caveat ?? null,
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
