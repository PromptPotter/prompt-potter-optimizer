import { describe, expect, it } from "vitest";
import { barsAreCourses, candidateViews, forkKeysOf } from "../candidate-views";
import type { DashboardCandidate, LineageNode, OverlapMember } from "@/lib/api";

// `candidateViews` decides WHICH served number each bar is allowed to show. Every rule it
// applies is a suppression or a source choice, and every one of them was prose until now —
// the card assembled these rows inline, where no test could reach them.

function node(
  over: Partial<LineageNode> & Pick<LineageNode, "kind" | "id" | "label">,
): LineageNode {
  return {
    parent_id: null,
    course_label: over.label,
    path: [],
    children: [],
    round: null,
    accuracy: null,
    composite_fitness: null,
    status: "",
    election_held: true,
    is_winner: false,
    theta: null,
    theta_se: null,
    evaluators: {},
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    scored_samples: null,
    expected_samples: null,
    cached_samples: null,
    lens_value: null,
    composite_rank: null,
    lens_rank: null,
    sample_set_accuracy: null,
    sample_set_n: null,
    divergence: null,
    divergent: false,
    course_kind: null,
    run_phase: "terminal",
    dataset_name: "",
    trigger: null,
    steered_by: null,
    task: null,
    best_accuracy: null,
    origin_accuracy: null,
    hearts: null,
    lives_cap: null,
    ...over,
  } as unknown as LineageNode;
}

function live(over: Partial<DashboardCandidate> & Pick<DashboardCandidate, "label">) {
  return {
    candidate_id: null,
    accuracy: null,
    composite_fitness: null,
    invalid: false,
    scored_samples: 0,
    cached_samples: 0,
    expected_samples: null,
    evaluators: {},
    changes_description: "",
    partial_reason: "",
    theta: null,
    theta_se: null,
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    matched_parent_accuracy: null,
    matched_parent_composite: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    is_winner: false,
    ...over,
  } as DashboardCandidate;
}

const EMPTY = {
  inflightByLabel: new Map<string, DashboardCandidate>(),
  sampleSet: null,
  lensSubsetExact: false,
  diagByLabel: new Map(),
  overlapByCandidate: new Map(),
  overlapSize: null,
};

const course = (kids: LineageNode[]) => node({ kind: "course", id: "c0", label: "root", children: kids });

describe("the half choice — tree unless it holds no measurement", () => {
  it("keeps the TREE's number even while a live row for the same label exists", () => {
    const views = candidateViews({
      ...EMPTY,
      viewedNode: course([
        node({ kind: "candidate", id: "a", label: "C1.1", round: 1, accuracy: 0.7, theta: 1.2 }),
      ]),
      inflightByLabel: new Map([["C1.1", live({ label: "C1.1", accuracy: 0.2, theta: -9 })]]),
    });
    expect(views[0]?.accuracy).toBe(0.7);
    expect(views[0]?.theta).toBe(1.2);
    expect(views[0]?.source).toBe("history");
  });

  it("takes the WHOLE live row when the tree has nothing — never field by field", () => {
    const views = candidateViews({
      ...EMPTY,
      viewedNode: course([node({ kind: "candidate", id: "a", label: "C1.1", round: 1 })]),
      inflightByLabel: new Map([
        [
          "C1.1",
          live({ label: "C1.1", accuracy: 0.4, theta: 0.9, is_winner: true, scored_samples: 8 }),
        ],
      ]),
    });
    // A bar and its whisker can never come from two different polling clocks.
    expect(views[0]).toMatchObject({
      source: "inflight",
      accuracy: 0.4,
      theta: 0.9,
      is_winner: true,
      n_samples: 8,
    });
  });

  // The one that matters: `INVALID_SCORES` reports a synthetic 0.0 for a candidate rejected
  // before it cost a sample. The tree withholds it; falling back to the live half would put
  // the fabricated number back on the bar and render it as "got everything wrong".
  it("does NOT fall back to an invalid live row's synthetic 0.0", () => {
    const views = candidateViews({
      ...EMPTY,
      viewedNode: course([node({ kind: "candidate", id: "a", label: "C1.1", round: 1 })]),
      inflightByLabel: new Map([
        ["C1.1", live({ label: "C1.1", accuracy: 0, invalid: true })],
      ]),
    });
    expect(views[0]?.accuracy).toBeNull();
    expect(views[0]?.started).toBe(false);
    expect(views[0]?.source).toBe("history");
  });
});

describe("course bars carry no verdict", () => {
  const runs = course([
    node({ kind: "course", id: "r1", label: "run-1", dataset_name: "justlogic", best_accuracy: 0.6, origin_accuracy: 0.3, composite_fitness: 0.9, composite_rank: 1 }),
    node({ kind: "course", id: "r2", label: "run-2", dataset_name: "justlogic", origin_accuracy: 0.3 }),
    node({ kind: "course", id: "r3", label: "run-3", dataset_name: "justlogic" }),
  ]);

  it("shows what a run reached, else what it started from, else blank", () => {
    const views = candidateViews({ ...EMPTY, viewedNode: runs });
    expect(views.map((v) => v.accuracy)).toEqual([0.6, 0.3, null]);
    // A cut that broke before measuring anything must render blank, never as its origin's.
    expect(views[2]?.started).toBe(false);
  });

  it("suppresses every election aggregate — /tree is a genealogy, not a verdict", () => {
    const views = candidateViews({
      ...EMPTY,
      viewedNode: runs,
      overlapSize: 20,
      overlapByCandidate: new Map([["r1", { candidate_id: "r1", accuracy: 0.55, total: 20 }]] as never),
    });
    expect(views[0]).toMatchObject({
      composite: null,
      compositeRank: null,
      overlapAccuracy: null,
      overlapN: null,
      // A run is not a scored row, so no promotion gate ever judged it against a parent.
      matchedParentLift: null,
      // A run is not a round, so it has no election to be pending on.
      electionPending: false,
    });
  });

  it("is what barsAreCourses reports, and a fork keeps its own key", () => {
    expect(barsAreCourses(runs)).toBe(true);
    const forks = course([
      node({ kind: "candidate", id: "a", label: "C1.1", round: 1 }),
      node({ kind: "candidate", id: "f", label: "C1.2", round: 1, course_kind: "fork" }),
    ]);
    expect(barsAreCourses(forks)).toBe(false);
    expect(forkKeysOf(forks).size).toBe(1);
  });
});

describe("a picked sample set moves the overlap bars, and nothing else", () => {
  const arm = course([
    node({
      kind: "candidate",
      id: "a",
      label: "C1.1",
      round: 1,
      accuracy: 0.7,
      theta: 1.2,
      composite_fitness: 0.65,
      cached_samples: 4,
      scored_samples: 12,
      matched_parent_lift: 0.09,
      sample_set_accuracy: 0.5,
      sample_set_n: 6,
    }),
  ]);

  // The whole point of the split: before the overlap series existed, "compare these on one
  // basis" could only be said by re-basing the metric bars themselves, which then forced θ,
  // the composite and the lift to be suppressed wholesale. They are on this candidate's OWN
  // cells and stay there whatever is picked here.
  it("leaves every metric bar on the candidate's own cells", () => {
    const views = candidateViews({ ...EMPTY, viewedNode: arm, sampleSet: [1, 2, 3, 4, 5, 6] });
    expect(views[0]).toMatchObject({
      accuracy: 0.7,
      theta: 1.2,
      composite: 0.65,
      cached_samples: 4,
      n_samples: 12,
      matchedParentLift: 0.09,
      // The one bar that moves — the SERVED re-score over the picked cells.
      overlapAccuracy: 0.5,
      overlapN: 6,
    });
  });

  it("blanks a candidate that answered only PART of the picked set", () => {
    const views = candidateViews({
      ...EMPTY,
      viewedNode: arm,
      sampleSet: [1, 2, 3, 4, 5, 6, 7, 8],
    });
    // 6 of 8 is a different exam from 8 of 8, and these bars exist to be differenced against
    // each other — so it is blank, never a short bar beside a full one.
    expect(views[0]).toMatchObject({ overlapAccuracy: null, overlapN: null, accuracy: 0.7 });
  });

  it("applies the same completeness rule to the SERVED reading", () => {
    const member = (total: number): Map<string, OverlapMember> =>
      new Map([["a", { round: 1, candidate_id: "a", label: "C1.1", accuracy: 0.55, total }]]);
    // A member whose cell came back unscoreable holds 19 of the line's 20 and is not readable
    // against the members that hold all of it.
    const on = (total: number) =>
      candidateViews({
        ...EMPTY,
        viewedNode: arm,
        overlapSize: 20,
        overlapByCandidate: member(total),
      })[0];
    expect(on(20)).toMatchObject({ overlapAccuracy: 0.55, overlapN: 20 });
    expect(on(19)).toMatchObject({ overlapAccuracy: null, overlapN: null });
  });

  // The one channel a picked set still moves besides the overlap bars, because the route
  // composes `lens` and `samples` in the same read. The silent harm is the FALSE arm: a
  // criterion naming an evaluator that only exists in the full-set snapshot re-scores partly on
  // the subset and partly on everything, and renders as a subset number either way. The TRUE arm
  // is pinned beside it so a future edit cannot collapse the pair back into "suppress whenever a
  // set is picked" — that dropped a value the server had already computed over these very cells.
  it("keeps a masked value that re-derives whole from the picked rows, drops one that cannot", () => {
    const lensed = course([
      node({
        kind: "candidate",
        id: "a",
        label: "C1.1",
        round: 1,
        sample_set_accuracy: 0.5,
        sample_set_n: 6,
        lens_value: 0.42,
        lens_rank: 1,
      }),
    ]);
    const picked = { ...EMPTY, viewedNode: lensed, sampleSet: [1, 2, 3, 4, 5, 6] };
    expect(candidateViews({ ...picked, lensSubsetExact: true })[0]).toMatchObject({
      lensValue: 0.42,
      lensRank: 1,
    });
    expect(candidateViews({ ...picked, lensSubsetExact: false })[0]).toMatchObject({
      lensValue: null,
      lensRank: null,
    });
  });

  it("gives a run no basis at all — the server decorates candidates only", () => {
    const runs = course([node({ kind: "course", id: "r1", label: "run-1", best_accuracy: 0.6 })]);
    const views = candidateViews({ ...EMPTY, viewedNode: runs, sampleSet: [1, 2] });
    expect(views[0]?.accuracy).toBe(0.6);
    expect(views[0]?.started).toBe(true);
    expect(views[0]?.overlapAccuracy).toBeNull();
  });
});

it("carries the election's lift verdict straight off the tree", () => {
  const views = candidateViews({
    ...EMPTY,
    viewedNode: course([
      node({
        kind: "candidate",
        id: "a",
        label: "C1.1",
        round: 1,
        accuracy: 0.6,
        matched_parent_lift: 0.12,
        matched_parent_lift_ci_lo: 0.04,
        matched_parent_lift_ci_hi: 0.2,
      }),
    ]),
  });
  // Served, never differenced here — `accuracy` minus anything is not this number.
  expect(views[0]).toMatchObject({
    matchedParentLift: 0.12,
    matchedParentLiftCiLo: 0.04,
    matchedParentLiftCiHi: 0.2,
  });
});

it("drops the tail a supersede cut retired — one round of three, never six", () => {
  const views = candidateViews({
    ...EMPTY,
    // What a fork's fold serves: the retired trio and the trio replacing it, one flat timeline
    // sharing labels. Both sides carry a distinct id, so nothing but `superseded_by` separates them.
    viewedNode: course([
      node({ kind: "candidate", id: "old1", label: "C10.1", round: 10, accuracy: 0.29, superseded_by: "cycle_fork" }),
      node({ kind: "candidate", id: "old2", label: "C10.2", round: 10, accuracy: 0.43, superseded_by: "cycle_fork" }),
      node({ kind: "candidate", id: "new1", label: "C10.1", round: 10, accuracy: 0.26 }),
      node({ kind: "candidate", id: "new2", label: "C10.2", round: 10 }),
    ]),
  });
  expect(views.map((v) => v.candidate_id)).toEqual(["new1", "new2"]);
  // `idx` numbers the bars actually drawn — it is the join to the dendrogram beneath them.
  expect(views.map((v) => v.idx)).toEqual([0, 1]);
});

it("says an uncrowned bar has not been judged yet, off the SERVED election flag", () => {
  const views = candidateViews({
    ...EMPTY,
    viewedNode: course([
      node({ kind: "candidate", id: "a", label: "C1.1", round: 1, accuracy: 0.4, election_held: false }),
      node({ kind: "candidate", id: "b", label: "C1.2", round: 1, accuracy: 0.4, election_held: true }),
    ]),
  });
  // A round that HELD reads exactly like one still scoring on `is_winner` alone.
  expect(views.map((v) => v.electionPending)).toEqual([true, false]);
});
