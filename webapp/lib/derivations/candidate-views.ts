// The candidates card's bar spine — the served tree's children of the VIEWED node, turned
// into the one row shape the bars, the dendrogram and the scoring-mask panel all read.
//
// Peer of `round-candidates.ts`, and the two answer different questions on purpose: that one
// normalizes ONE cycle's own rounds out of `dashboard.json` for the sample-scoped surfaces;
// this one walks the genealogy, which is the only source that can express what hangs off a
// candidate — a fork's contributed attempts, an L4 inner run, a course.
//
// Every number here is SERVED. Nothing is scored, ranked or re-based; the rules below only
// choose WHICH served number a bar is allowed to show, and suppress the ones whose basis the
// chart is no longer on.

import type {
  DashboardCandidate,
  DiagnosticRunRecord,
  LineageNode,
  OverlapMember,
} from "@/lib/api/types";
import type { CandidateView } from "@/lib/types";
import { panelCellLabel } from "./inner-panel";
import { nodeKeyOf } from "./lineage-candidates";

// The mask re-scores a candidate's measured ROWS. A course is a run, not a scored row, so a
// view whose bars are courses (an L4 candidate's inner cells) has nothing to slice — the
// server decorates candidates only. Reading `sample_set_accuracy` off a course yielded null,
// which `started` then rendered as "never ran": the one lie this card must not tell. Children
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

// The sample count is worth painting over a bar only where it is NEWS. Two cases: a bar
// still under its own announced budget, and one whose panel is shorter than the fullest in
// its round — PoBB leader-lock stopping a single arm at 8 while its siblings ran 20 is
// exactly what this overlay exists to say. Every bar's count stays in the tooltip footer,
// which is the denominator of record; painting it on all 25 was noise that hid the one arm
// that stopped short. `null` = nothing to say here.
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
  // Label → the row `dash.current_round` is scoring right now. Keyed by label because a
  // course's OWN candidates keep their minted label, and `dash` is the viewed course's
  // telemetry.
  inflightByLabel: ReadonlyMap<string, DashboardCandidate>;
  // The fixed sample set the operator pinned, or null. Re-bases every bar.
  sampleSet: number[] | null;
  // Whether the mask in force re-derives whole from the masked rows (`scoring-mask::subsetExactFor`).
  // Decided off the evaluator registry, not here — this only says which of the two the bars carry.
  lensSubsetExact: boolean;
  diagByLabel: ReadonlyMap<string, DiagnosticRunRecord>;
  overlapByCandidate: ReadonlyMap<string, OverlapMember>;
}

export function candidateViews({
  viewedNode,
  inflightByLabel,
  sampleSet,
  lensSubsetExact,
  diagByLabel,
  overlapByCandidate,
}: CandidateViewsInput): CandidateView[] {
  const sliced = sampleSet != null && !barsAreCourses(viewedNode);
  // ONE half per bar, ALL-OR-NOTHING, chosen once here: the tree, unless it has no
  // measurement for this candidate yet. That single condition is the whole rule now — the
  // ledger mints a candidate before it measures one, but only snapshots the score at
  // completion, so a bar mid-scoring is the one thing the tree cannot answer.
  //
  // It used to also prefer the live half for the whole of an OPEN round, because the crown
  // rode the round's CLOSE record while `elect_round_winner` decides at the end of SCORING —
  // a whole `l1_critique` call earlier. The election has its own ledger record at its own
  // coordinate now, so the tree crowns when the election does and that window is gone.
  return (viewedNode?.children ?? []).map<CandidateView>((n, i) => {
    const isCourse = n.kind === "course";
    // A course shows what it reached, else what it started from. A cut that broke before
    // measuring anything has no number and must render blank, never as its origin's.
    const own = isCourse ? (n.best_accuracy ?? n.origin_accuracy) : n.accuracy;
    const live = isCourse ? undefined : inflightByLabel.get(n.label);
    // An INVALID candidate was rejected before it cost a sample, and the served row reports
    // `INVALID_SCORES`' synthetic 0.0. The tree withholds that number, so falling back to the
    // live half would put the fabricated one back on the bar.
    const useLive = live != null && !live.invalid && own == null;
    // The chosen half. Every measured number below reads off THIS, so a bar and its whisker
    // can never come from two different polling clocks.
    const m = useLive ? live : n;
    // Slice mode reads the SERVED scorer-faithful value. Election aggregates can't be
    // re-sliced per sample, so they are suppressed rather than shown on a different basis
    // than the bar beside them.
    const accuracy = sliced
      ? n.sample_set_accuracy
      : isCourse
        ? (own ?? null)
        : (m.accuracy ?? null);
    const label = isCourse ? (n.task ? panelCellLabel(n.task) : n.dataset_name) : n.label;
    return {
      key: nodeKeyOf(n),
      round: n.round ?? 0,
      idx: i,
      candidate_id: n.id,
      label,
      accuracy,
      composite: sliced || isCourse ? null : (m.composite_fitness ?? null),
      theta: sliced ? null : (m.theta ?? null),
      theta_se: sliced ? null : (m.theta_se ?? null),
      // From the same row as the bar above it, whichever half that was.
      meanFitnessCiLo: sliced ? null : (m.mean_fitness_ci_lo ?? null),
      meanFitnessCiHi: sliced ? null : (m.mean_fitness_ci_hi ?? null),
      // The election's verdict, suppressed like the rest of them: it is a lift over the cells
      // this candidate measured, and re-basing the bars leaves it describing a different set.
      matchedParentLift: sliced || isCourse ? null : n.matched_parent_lift,
      matchedParentLiftCiLo: sliced || isCourse ? null : n.matched_parent_lift_ci_lo,
      matchedParentLiftCiHi: sliced || isCourse ? null : n.matched_parent_lift_ci_hi,
      evaluators: n.evaluators,
      is_winner: m.is_winner ?? false,
      n_samples: sliced ? n.sample_set_n : (m.scored_samples ?? null),
      n_expected: sliced ? (sampleSet?.length ?? null) : (m.expected_samples ?? null),
      cached_samples: sliced ? null : (m.cached_samples ?? null),
      source: useLive ? "inflight" : "history",
      // The one masked value that SURVIVES a slice, when every evaluator in it re-derives from
      // the rows: the server composed `lens` + `samples` in the same read, so the number beside
      // the sliced bars is already on their own cells. Suppressing it wholesale was the client
      // declining the answer to its own request; suppressing it when the criterion mixes in a
      // snapshot-only evaluator is the honest half of that rule, kept.
      lensValue: sliced && !lensSubsetExact ? null : n.lens_value,
      // Ranks follow their values exactly: suppressed on the same conditions, or a bar would
      // carry a position in an ordering whose number it is not showing.
      compositeRank: sliced || isCourse ? null : n.composite_rank,
      lensRank: sliced && !lensSubsetExact ? null : n.lens_rank,
      started: accuracy != null,
      // SERVED, not inferred from whether the round has closed. `is_winner: false` says
      // nothing on its own — a round that HELD crowned nobody and every bar in it reads the
      // same as one still scoring — and the browser used to guess between them off
      // `dash.rounds[]`, which reported every held round as undecided for the rest of the run.
      electionPending: !isCourse && !n.election_held,
      diag: diagView(diagByLabel.get(label)),
      // Suppressed while sliced or on a course, exactly like the other served aggregates: this
      // is a rate over the LINE's own set, and re-basing the bars onto a different one leaves
      // it describing cells the chart is no longer showing.
      overlapAccuracy: sliced || isCourse ? null : (overlapByCandidate.get(n.id)?.accuracy ?? null),
      overlapN: sliced || isCourse ? null : (overlapByCandidate.get(n.id)?.total ?? null),
    };
  });
}
