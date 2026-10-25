import { describe, expect, it } from "vitest";
import { barLevel, barsAreCourses, candidateBars } from "../candidate-views";
import type {
  ArmReading,
  CourseFacts,
  HierarchyCandidate,
  SubtreeCourse,
  VerificationMark,
} from "@/lib/api";
import { encodeCyclePath } from "@/lib/ids";
import {
  armReading,
  courseTags,
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCourse,
  ownLevel,
  pairedReading,
  roundsOf,
  runStanding,
  supersededTag,
} from "@/lib/test-fixtures";

const standsAt = (rate: number) =>
  runStanding(1, "C1.1", { vs_origin: pairedReading(rate - 0.5, [-1, 1], { rateA: 0.5 }) });

const EMPTY = {
  inflightByKey: new Map<string, ArmReading>(),
  sampleSet: null,
  marks: [],
};

type ReadingOver = Parameters<typeof armReading>[0];

const OUTER = [{ campaignId: "camp__aaaaaa", cycleId: "cycle_root" }];
const C = encodeCyclePath(OUTER);

const arm = (
  id: string,
  label: string,
  reading: ReadingOver = {},
  facts: Partial<HierarchyCandidate["facts"]> = {},
  over: Partial<HierarchyCandidate> = {},
) =>
  hierarchyCandidate(
    C,
    {
      id,
      reading: armReading({ election: { held: true }, ...reading, arm: { round: 1, label, key: id } }),
      ...facts,
    },
    over,
  );

const course = (kids: HierarchyCandidate[]) =>
  subtreeCourse({ address: C, label: "root", children: roundsOf(C, kids) });
const runsUnder = (runs: SubtreeCourse[]) =>
  arm("a", "C1.1", {}, {}, {
    children: [subtreeCampaign({ address: `${C}~inner__1`, children: runs })],
  });
// `label` as the server words a course measuring a task: the task's own name.
const run = (id: string, facts: Partial<CourseFacts>, label?: string) =>
  subtreeCourse(
    {
      address: encodeCyclePath([...OUTER, { campaignId: "inner__1", cycleId: id }]),
      tags: courseTags("inner", "justlogic"),
      ...(label === undefined ? {} : { label }),
    },
    { mint: "inner", ...facts },
  );

describe("the half choice — the tree's, once its walk is decided", () => {
  it("keeps the TREE's reading even while a live row for the same arm exists", () => {
    const tree = arm("a", "C1.1", { state: "measured", own: ownLevel(0.7) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([tree]),
      inflightByKey: new Map([["a", armReading({ own: ownLevel(0.2) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.facts.reading);
    expect(barLevel("accuracy", bars[0]!)).toBe(0.7);
    expect(bars[0]).toMatchObject({ source: "history" });
  });

  it("takes the WHOLE live reading while the tree's walk is undecided — never field by field", () => {
    const live = armReading({ own: ownLevel(0.4, 8), election: { selected: true } });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([arm("a", "C1.1")]),
      inflightByKey: new Map([["a", live]]),
    });
    // A bar and its whisker can never come from two different polling clocks.
    expect(bars[0]?.reading).toBe(live);
    expect(bars[0]).toMatchObject({ source: "inflight" });
    expect(barLevel("accuracy", bars[0]!)).toBe(0.4);
  });

  it("keeps a decided arm's blank over a live row", () => {
    const tree = arm("a", "C1.1", { state: "cut", end: "invalid" });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([tree]),
      inflightByKey: new Map([["a", armReading({ own: ownLevel(0.3) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.facts.reading);
    expect(barLevel("accuracy", bars[0]!)).toBeNull();
    expect(bars[0]).toMatchObject({ source: "history" });
  });

  it("joins the live half on the arm's key, never a label", () => {
    const live = armReading({ own: ownLevel(0.4) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: course([arm("f", "C1.2")]),
      inflightByKey: new Map([["f", live], ["C1.2", armReading({ own: ownLevel(0.9) })]]),
    });
    expect(bars[0]).toMatchObject({ label: "C1.2", source: "inflight" });
    expect(barLevel("accuracy", bars[0]!)).toBe(0.4);
  });
});

describe("course bars carry no verdict", () => {
  const runs = runsUnder([
    run("r1", { standing: standsAt(0.6) }),
    run("r2", { task: "justlogic/seed-2", standing: standsAt(0.3) }, "seed-2"),
    run("r3", { standing: runStanding(0, "C0") }),
  ]);

  it("draws a run at its selection's served rate against its origin, blank where no pair was read", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs });
    expect(bars.map((b) => barLevel("accuracy", b))).toEqual([0.6, 0.3, null]);
    expect(bars.map((b) => barLevel("composite", b))).toEqual([null, null, null]);
    expect(bars.map((b) => b.label)).toEqual(["justlogic", "seed-2", "justlogic"]);
  });

  it("holds no reading and no basis — a run is not a scored row", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs, sampleSet: [1, 2] });
    expect(bars[0]).toMatchObject({ reading: null, node: { level: "course" } });
    expect(bars[0] && "overlap" in bars[0]).toBe(false);
  });

  it("is what barsAreCourses reports; a course's own arms are not runs", () => {
    expect(barsAreCourses(runs)).toBe(true);
    expect(barsAreCourses(course([arm("a", "C1.1")]))).toBe(false);
    expect(barsAreCourses(arm("a", "C1.1"))).toBe(false);
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
          on_origin_panel: { arm: { round: 1, label: "C1.1", key: "a" }, rate: 0.55, n },
        },
        { sample_set_accuracy: onSet, sample_set_n: 6 },
      ),
    ]);

  it("re-bases on a picked sample set and leaves the level alone", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: onPanel(20), sampleSet: [1, 2, 3, 4, 5, 6] });
    expect(bars[0]).toMatchObject({ overlap: { rate: 0.5, n: 6 } });
    expect(barLevel("accuracy", bars[0]!)).toBe(0.7);
  });

  it("blanks a candidate served no rate on the picked set", () => {
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: onPanel(20, null),
      sampleSet: [1, 2, 3, 4, 5, 6, 7, 8],
    });
    // 6 of 8 is a different exam from 8 of 8, so the server holds the rate back.
    expect(bars[0]).toMatchObject({ overlap: null });
    expect(barLevel("accuracy", bars[0]!)).toBe(0.7);
  });

  it("draws the SERVED origin-panel rate as it came", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: onPanel(20) });
    expect(bars[0]).toMatchObject({ overlap: { rate: 0.55, n: 20 } });
  });
});

it("drops the tail a supersede cut retired — one round of three, never six", () => {
  const retired = { tags: [supersededTag(`${C}_fork`)] };
  const live = [arm("new1", "C10.1", { own: ownLevel(0.26) }), arm("new2", "C10.2")];
  const bars = candidateBars({
    ...EMPTY,
    // Both sides share labels, so nothing but the `superseded` tag separates them.
    viewedNode: course([
      arm("old1", "C10.1", { own: ownLevel(0.29) }, {}, retired),
      arm("old2", "C10.2", { own: ownLevel(0.43) }, {}, retired),
      ...live,
    ]),
  });
  expect(bars.map((b) => b.key)).toEqual(live.map((c) => c.address));
  // `idx` numbers the bars actually drawn — it is the join to the dendrogram beneath them.
  expect(bars.map((b) => b.idx)).toEqual([0, 1]);
});

it("hangs each pass on the arm it measures, by kind, and on no other", () => {
  const mark = (kind: VerificationMark["kind"], key: string): VerificationMark => ({
    kind,
    arm: { key, round: 1, label: "C1.2" },
    state: "running",
    rows: 10,
    scored: 0,
    level: null,
    failure: null,
  });
  const bench = mark("bench", "b");
  const verify = mark("verify", "a");
  const bars = candidateBars({
    ...EMPTY,
    viewedNode: course([arm("a", "C1.1"), arm("b", "C1.2")]),
    marks: [bench, verify],
  });
  expect(bars.map((b) => b.reading && [b.verify, b.bench])).toEqual([
    [verify, null],
    [null, bench],
  ]);
});
