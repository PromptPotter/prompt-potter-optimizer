import { describe, expect, it } from "vitest";
import { barsAreCourses, candidateViews, forkKeysOf } from "../candidate-views";
import type { DashboardCandidate, LineageNode } from "@/lib/api";

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
    cumulative_theta: null,
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
  diagByLabel: new Map(),
  overlapByCandidate: new Map(),
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

describe("the fixed sample set re-bases the bars", () => {
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
      sample_set_accuracy: 0.5,
      sample_set_n: 6,
    }),
  ]);

  it("reads the SERVED sliced value and suppresses what cannot be re-sliced", () => {
    const views = candidateViews({ ...EMPTY, viewedNode: arm, sampleSet: [1, 2, 3, 4, 5, 6] });
    expect(views[0]).toMatchObject({
      accuracy: 0.5,
      n_samples: 6,
      n_expected: 6,
      // Election aggregates can't be re-sliced per sample, so they go rather than appear on
      // a different basis than the bar beside them.
      theta: null,
      composite: null,
      cached_samples: null,
      // The lift is over the cells this candidate measured; re-basing the bars leaves it
      // describing a different set.
      matchedParentLift: null,
    });
  });

  it("does not slice a run — the server decorates candidates only", () => {
    const runs = course([node({ kind: "course", id: "r1", label: "run-1", best_accuracy: 0.6 })]);
    const views = candidateViews({ ...EMPTY, viewedNode: runs, sampleSet: [1, 2] });
    // Reading `sample_set_accuracy` off a course yields null, which `started` would then
    // render as "never ran" — the one lie this card must not tell.
    expect(views[0]?.accuracy).toBe(0.6);
    expect(views[0]?.started).toBe(true);
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
