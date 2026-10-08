// The served tree's children of the VIEWED node as the one row shape the bars, dendrogram and
// mask panel read. Every number is served; a picked sample set reaches only the overlap channel.

import type {
  BenchPassProgress,
  DashboardCandidate,
  LineageNode,
  OverlapMember,
  VerifyReading,
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

// Each candidate's last `verify`, keyed by label, off its closed round row. A dashboard an older
// build wrote, or one replayed at a past moment, carries none.
export function verifyByLabel(
  rounds: readonly { candidates: readonly DashboardCandidate[] }[],
): Map<string, VerifyReading> {
  const m = new Map<string, VerifyReading>();
  for (const r of rounds) {
    for (const c of r.candidates) {
      if (c.verify != null) m.set(c.label, c.verify);
    }
  }
  return m;
}

// Each candidate's held-out reading, keyed by label: closed off its round row, and the pass in
// flight on the candidate it names. Both are served; nothing here is folded from rows.
export function benchByLabel(
  rounds: readonly { candidates: readonly DashboardCandidate[] }[],
  pass: BenchPassProgress | null | undefined,
): Map<string, NonNullable<CandidateView["bench"]>> {
  const m = new Map<string, NonNullable<CandidateView["bench"]>>();
  for (const r of rounds) {
    for (const c of r.candidates) {
      const b = c.bench;
      if (b == null) continue;
      const col = b[b.headline];
      m.set(c.label, {
        accuracy: col?.value ?? null,
        ciLo: col?.ci_lo ?? null,
        ciHi: col?.ci_hi ?? null,
        scored: b.n_scored,
      });
    }
  }
  if (pass?.label != null) {
    m.set(pass.label, {
      accuracy: pass.accuracy ?? null,
      ciLo: null,
      ciHi: null,
      scored: pass.scored,
      rows: pass.rows,
    });
  }
  return m;
}

export interface CandidateViewsInput {
  viewedNode: LineageNode | undefined;
  // Keyed by label: a course's own candidates keep their minted label, and `dash` is its telemetry.
  inflightByLabel: ReadonlyMap<string, DashboardCandidate>;
  // null ⇒ the served reading.
  sampleSet: number[] | null;
  verifyByLabel: ReadonlyMap<string, VerifyReading>;
  benchByLabel: ReadonlyMap<string, NonNullable<CandidateView["bench"]>>;
  overlapByCandidate: ReadonlyMap<string, OverlapMember>;
  // The denominator a member must match to be readable.
  overlapSize: number | null;
  // `LiveDashboardState.stamps_theta`.
  stampsTheta: boolean;
}

export function candidateViews({
  viewedNode,
  inflightByLabel,
  sampleSet,
  verifyByLabel,
  benchByLabel,
  overlapByCandidate,
  overlapSize,
  stampsTheta,
}: CandidateViewsInput): CandidateView[] {
  const pickedSet = sampleSet != null && !barsAreCourses(viewedNode);
  const basis = pickedSet ? (sampleSet?.length ?? null) : overlapSize;
  // ONE half per bar, all-or-nothing: the tree, unless it has no score yet (the ledger snapshots
  // one only at completion). Live side of each supersede cut only — retired tails double a round.
  return splitRetired(viewedNode?.children ?? []).live.map<CandidateView>((n, i) => {
    const isCourse = n.kind === "course";
    // A course is drawn at its served headline; one that measured nothing serves none and
    // renders blank, never as its origin's number.
    const own = isCourse ? n.headline_accuracy : n.accuracy;
    const live = isCourse ? undefined : inflightByLabel.get(n.label);
    // An INVALID candidate reports `INVALID_SCORES`' synthetic 0.0 and the tree withholds it, so
    // falling back to the live half would put the fabricated number back on the bar.
    const useLive = live != null && live.outcome !== "invalid" && own == null;
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
      theta: stampsTheta ? (m.theta ?? null) : null,
      theta_se: stampsTheta ? (m.theta_se ?? null) : null,
      thetaCaveat: stampsTheta ? (m.theta_caveat ?? null) : null,
      meanFitnessCiLo: m.mean_fitness_ci_lo ?? null,
      meanFitnessCiHi: m.mean_fitness_ci_hi ?? null,
      referenceLift: isCourse ? null : n.reference_lift,
      referenceLiftCiLo: isCourse ? null : n.reference_lift_ci_lo,
      referenceLiftCiHi: isCourse ? null : n.reference_lift_ci_hi,
      referenceLiftSide: isCourse ? null : n.reference_lift_side,
      is_selected: m.is_selected ?? false,
      n_samples: m.scored_samples ?? null,
      n_expected: m.expected_samples ?? null,
      // Off the same half as the counts it judges.
      panelCut: m.panel_cut === true,
      cached_samples: m.cached_samples ?? null,
      source: useLive ? "inflight" : "history",
      // The route composes `lens` and `samples` in one read, so a picked set masks this number
      // too — the one channel besides the overlap bars that a pick still moves.
      lensValue: n.lens_value,
      // Ranks follow their values exactly, or a bar carries a position in an ordering whose
      // number it is not showing.
      compositeRank: isCourse ? null : n.composite_rank,
      lensRank: n.lens_rank,
      started: accuracy != null,
      // SERVED, never inferred from whether the round has closed: a round that HELD crowned
      // nobody and reads exactly like one still scoring.
      electionPending: !isCourse && !n.election_held,
      crown: n.crown,
      verify: isCourse ? undefined : verifyByLabel.get(n.label),
      bench: isCourse ? undefined : benchByLabel.get(n.label),
      overlapAccuracy: whole
        ? pickedSet
          ? (n.sample_set_accuracy ?? null)
          : (member?.accuracy ?? null)
        : null,
      overlapN: whole ? onBasis : null,
    };
  });
}
