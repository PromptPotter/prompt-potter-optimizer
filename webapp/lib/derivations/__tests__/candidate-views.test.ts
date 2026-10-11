import { describe, expect, it } from "vitest";
import { barsAreCourses, candidateBars } from "../candidate-views";
import type { ArmNode, ArmReading, BenchPassProgress, CourseNode, ForkStamp } from "@/lib/api";
import {
  armNode,
  armReading,
  courseNode,
  ownLevel,
  pairedReading,
  runStanding,
} from "@/lib/test-fixtures";

const standsAt = (rate: number) =>
  runStanding(1, "C1.1", { vs_origin: pairedReading(rate - 0.5, [-1, 1], { rateA: 0.5 }) });

const EMPTY = {
  inflightByLabel: new Map<string, ArmReading>(),
  sampleSet: null,
  benchPass: null,
};

type ReadingOver = Parameters<typeof armReading>[0];

const arm = (id: string, label: string, reading: ReadingOver = {}, over: Partial<ArmNode> = {}) =>
  armNode({
    id,
    label,
    reading: armReading({
      election: { held: true },
      ...reading,
      arm: { round: 1, label, candidate_id: id },
    }),
    ...over,
  });

const course = (kids: ArmNode[]) => courseNode({ id: "c0", label: "root", children: kids });
const runsUnder = (runs: CourseNode[]) => armNode({ id: "a", children: runs });

describe("the half choice — the tree's, once its walk is decided", () => {
  it("keeps the TREE's reading even while a live row for the same label exists", () => {
    const tree = arm("a", "C1.1", { outcome: "measured", own: ownLevel(0.7) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([tree]),
      inflightByLabel: new Map([["C1.1", armReading({ own: ownLevel(0.2) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.reading);
    expect(bars[0]?.level).toBe(0.7);
    expect(bars[0]?.source).toBe("history");
  });

  it("takes the WHOLE live reading while the tree's walk is undecided — never field by field", () => {
    const live = armReading({ own: ownLevel(0.4, 8), election: { selected: true } });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([arm("a", "C1.1")]),
      inflightByLabel: new Map([["C1.1", live]]),
    });
    // A bar and its whisker can never come from two different polling clocks.
    expect(bars[0]?.reading).toBe(live);
    expect(bars[0]).toMatchObject({ source: "inflight", level: 0.4 });
  });

  it("keeps a decided arm's blank over a live row", () => {
    const tree = arm("a", "C1.1", { outcome: "invalid" });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([tree]),
      inflightByLabel: new Map([["C1.1", armReading({ own: ownLevel(0.3) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.reading);
    expect(bars[0]?.level).toBeNull();
    expect(bars[0]?.source).toBe("history");
  });

  it("joins the live half on the arm's own label, not its timeline position", () => {
    const forked = armNode({
      id: "f",
      label: "C1.2",
      reading: armReading({ arm: { round: 1, label: "C1.1", candidate_id: "f" } }),
    });
    const live = armReading({ own: ownLevel(0.4) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([forked]),
      inflightByLabel: new Map([["C1.1", live]]),
    });
    expect(bars[0]).toMatchObject({ label: "C1.2", source: "inflight", level: 0.4 });
  });
});

describe("course bars carry no verdict", () => {
  const runs = runsUnder([
    courseNode({ id: "r1", dataset_name: "justlogic", run_standing: standsAt(0.6) }),
    courseNode({ id: "r2", task: "justlogic/seed-2", run_standing: standsAt(0.3) }),
    courseNode({ id: "r3", dataset_name: "justlogic", run_standing: runStanding(0, "C0") }),
  ]);

  it("draws a run at its selection's served rate against its origin, blank where no pair was read", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs });
    expect(bars.map((b) => b.level)).toEqual([0.6, 0.3, null]);
    expect(bars.map((b) => b.label)).toEqual(["justlogic", "seed-2", "justlogic"]);
  });

  it("holds no arm, no reading and no basis — a run is not a scored row", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs, sampleSet: [1, 2] });
    expect(bars[0]).toMatchObject({ arm: null, reading: null, overlap: null, benchPass: null });
  });

  it("is what barsAreCourses reports, and a fork keeps its own key", () => {
    expect(barsAreCourses(runs)).toBe(true);
    const fork: ForkStamp = {
      kind: "fork",
      trigger: "",
      direction: null,
      steered_by: null,
      status: { label: "Running", mark: "running" },
      cut_from: null,
    };
    const forks = course([arm("a", "C1.1"), arm("f", "C1.2", {}, { fork })]);
    expect(barsAreCourses(forks)).toBe(false);
  });
});

describe("the overlap channel — drawn only on the WHOLE basis", () => {
  const onPanel = (n: number, onSet: number | null = 0.5) =>
    course([
      arm(
        "a",
        "C1.1",
        {
          own: ownLevel(0.7, 12),
          on_origin_panel: { arm: { round: 1, label: "C1.1", candidate_id: "a" }, rate: 0.55, n },
        },
        { sample_set_accuracy: onSet, sample_set_n: 6 },
      ),
    ]);

  it("re-bases on a picked sample set and leaves the level alone", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: onPanel(20), sampleSet: [1, 2, 3, 4, 5, 6] });
    expect(bars[0]).toMatchObject({ level: 0.7, overlap: { rate: 0.5, n: 6 } });
  });

  it("blanks a candidate served no rate on the picked set", () => {
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: onPanel(20, null),
      sampleSet: [1, 2, 3, 4, 5, 6, 7, 8],
    });
    // 6 of 8 is a different exam from 8 of 8, so the server holds the rate back.
    expect(bars[0]).toMatchObject({ level: 0.7, overlap: null });
  });

  it("draws the SERVED origin-panel rate as it came", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: onPanel(20) });
    expect(bars[0]?.overlap).toEqual({ rate: 0.55, n: 20 });
  });
});

it("drops the tail a supersede cut retired — one round of three, never six", () => {
  const bars = candidateBars({
    ...EMPTY,
    // Both sides carry a distinct id and share labels, so nothing but `superseded_by` separates them.
    viewedNode: course([
      arm("old1", "C10.1", { own: ownLevel(0.29) }, { superseded_by: "cycle_fork" }),
      arm("old2", "C10.2", { own: ownLevel(0.43) }, { superseded_by: "cycle_fork" }),
      arm("new1", "C10.1", { own: ownLevel(0.26) }),
      arm("new2", "C10.2"),
    ]),
  });
  expect(bars.map((b) => b.arm?.id)).toEqual(["new1", "new2"]);
  // `idx` numbers the bars actually drawn — it is the join to the dendrogram beneath them.
  expect(bars.map((b) => b.idx)).toEqual([0, 1]);
});

it("marks the bench pass in flight on the arm it grades, and no other", () => {
  const pass: BenchPassProgress = {
    subject: "selected",
    label: "C1.2",
    sp_hash: "",
    round: 1,
    rows: 10,
    scored: 0,
    accuracy: null,
  };
  const bars = candidateBars({
    ...EMPTY,
    viewedNode: course([arm("a", "C1.1"), arm("b", "C1.2")]),
    benchPass: pass,
  });
  expect(bars.map((b) => b.benchPass)).toEqual([null, pass]);
});
