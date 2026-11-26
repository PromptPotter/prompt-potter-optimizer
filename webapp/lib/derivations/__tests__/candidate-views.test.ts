import { describe, expect, it } from "vitest";
import { barLevel, barsAreCycles, candidateBars } from "../candidate-views";
import { EMPTY_LINEAGE, indexLineage } from "../lineage-candidates";
import type {
  ArmReading,
  CycleEntryFacts,
  HierarchyCandidate,
  SubtreeCycle,
  VerificationMark,
} from "@/lib/api/types";
import { encodeCyclePath } from "@/lib/ids";
import {
  armReading,
  cycleTags,
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCycle,
  ownLevel,
  pairedReading,
  roundsOf,
  runStanding,
  supersededTag,
} from "@/lib/test-fixtures";

const standsAt = (rate: number) =>
  runStanding(1, "C1.1", { vs_origin: pairedReading(rate - 0.5, [-1, 1], { rateA: 0.5 }) });

const EMPTY = {
  index: EMPTY_LINEAGE,
  inflightByKey: new Map<string, ArmReading>(),
  sampleSet: null,
  marks: [],
  every: true,
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

const cycle = (kids: HierarchyCandidate[]) =>
  subtreeCycle({ address: C, label: "root", children: roundsOf(C, kids) });
const runsUnder = (runs: SubtreeCycle[]) =>
  arm("a", "C1.1", {}, {}, {
    children: [subtreeCampaign({ address: `${C}~inner__1`, children: runs })],
  });
// `label` as the server words a cycle measuring a task: the task's own name.
const run = (id: string, facts: Partial<CycleEntryFacts>, label?: string) =>
  subtreeCycle(
    {
      address: encodeCyclePath([...OUTER, { campaignId: "inner__1", cycleId: id }]),
      tags: cycleTags("inner", "justlogic"),
      ...(label === undefined ? {} : { label }),
    },
    { mint: "inner", ...facts },
  );

describe("the half choice — the tree's, once its walk is decided", () => {
  it("keeps the TREE's reading even while a live row for the same arm exists", () => {
    const tree = arm("a", "C1.1", { state: "measured", own: ownLevel(0.7) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: cycle([tree]),
      inflightByKey: new Map([["a", armReading({ own: ownLevel(0.2) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.facts.reading);
    expect(barLevel("fitness", bars[0]!)).toBe(0.7);
    expect(bars[0]).toMatchObject({ source: "history" });
  });

  it("takes the WHOLE live reading while the tree's walk is undecided — never field by field", () => {
    const live = armReading({ own: ownLevel(0.4, 8), election: { selected: true } });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: cycle([arm("a", "C1.1")]),
      inflightByKey: new Map([["a", live]]),
    });
    // A bar and its whisker can never come from two different polling clocks.
    expect(bars[0]?.reading).toBe(live);
    expect(bars[0]).toMatchObject({ source: "inflight" });
    expect(barLevel("fitness", bars[0]!)).toBe(0.4);
  });

  it("keeps a decided arm's blank over a live row", () => {
    const tree = arm("a", "C1.1", { state: "cut", end: "invalid" });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: cycle([tree]),
      inflightByKey: new Map([["a", armReading({ own: ownLevel(0.3) })]]),
    });
    expect(bars[0]?.reading).toBe(tree.facts.reading);
    expect(barLevel("fitness", bars[0]!)).toBeNull();
    expect(bars[0]).toMatchObject({ source: "history" });
  });

  it("joins the live half on the arm's key, never a label", () => {
    const live = armReading({ own: ownLevel(0.4) });
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: cycle([arm("f", "C1.2")]),
      inflightByKey: new Map([["f", live], ["C1.2", armReading({ own: ownLevel(0.9) })]]),
    });
    expect(bars[0]).toMatchObject({ label: "C1.2", source: "inflight" });
    expect(barLevel("fitness", bars[0]!)).toBe(0.4);
  });
});

describe("cycle bars carry no verdict", () => {
  const runs = runsUnder([
    run("r1", { standing: standsAt(0.6) }),
    run("r2", { task: "justlogic/seed-2", standing: standsAt(0.3) }, "seed-2"),
    run("r3", { standing: runStanding(0, "C0") }),
  ]);

  it("draws a run at its selection's served rate against its origin, blank where no pair was read", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs });
    expect(bars.map((b) => barLevel("fitness", b))).toEqual([0.6, 0.3, null]);
    expect(bars.map((b) => barLevel("composite", b))).toEqual([null, null, null]);
    expect(bars.map((b) => b.label)).toEqual(["justlogic", "seed-2", "justlogic"]);
  });

  it("holds no reading and no basis — a run is not a scored row", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: runs, sampleSet: [1, 2] });
    expect(bars[0]).toMatchObject({ reading: null, node: { entry_kind: "cycle" } });
    expect(bars[0] && "overlap" in bars[0]).toBe(false);
  });

  it("is what barsAreCycles reports; a cycle's own arms are not runs", () => {
    expect(barsAreCycles(runs)).toBe(true);
    expect(barsAreCycles(cycle([arm("a", "C1.1")]))).toBe(false);
    expect(barsAreCycles(arm("a", "C1.1"))).toBe(false);
  });
});

describe("the overlap channel — drawn only on the WHOLE basis", () => {
  const onPanel = (n: number, onSet: number | null = 0.5) =>
    cycle([
      arm(
        "a",
        "C1.1",
        {
          own: ownLevel(0.7, 12),
          on_origin_panel: { arm: { round: 1, label: "C1.1", key: "a" }, rate: 0.55, unit: "rate", n },
        },
        { sample_set_fitness: onSet, sample_set_n: 6 },
      ),
    ]);

  it("re-bases on a picked sample set and leaves the level alone", () => {
    const bars = candidateBars({ ...EMPTY, viewedNode: onPanel(20), sampleSet: [1, 2, 3, 4, 5, 6] });
    expect(bars[0]).toMatchObject({ overlap: { rate: 0.5, n: 6 } });
    expect(barLevel("fitness", bars[0]!)).toBe(0.7);
  });

  it("blanks a candidate served no rate on the picked set", () => {
    const bars = candidateBars({
      ...EMPTY,
      viewedNode: onPanel(20, null),
      sampleSet: [1, 2, 3, 4, 5, 6, 7, 8],
    });
    // 6 of 8 is a different exam from 8 of 8, so the server holds the rate back.
    expect(bars[0]).toMatchObject({ overlap: null });
    expect(barLevel("fitness", bars[0]!)).toBe(0.7);
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
    viewedNode: cycle([
      arm("old1", "C10.1", { own: ownLevel(0.29) }, {}, retired),
      arm("old2", "C10.2", { own: ownLevel(0.43) }, {}, retired),
      ...live,
    ]),
  });
  expect(bars.map((b) => b.key)).toEqual(live.map((c) => c.address));
  // `idx` numbers the bars actually drawn — it is the join to the dendrogram beneath them.
  expect(bars.map((b) => b.idx)).toEqual([0, 1]);
});

it("leads a fork's bars with its line below the cut, the parent's candidates by address", () => {
  const below = [arm("c0", "C0"), arm("p1", "C1.1")];
  const forkAt = encodeCyclePath([{ campaignId: "camp__aaaaaa", cycleId: "cycle_root_fork_1" }]);
  const own = hierarchyCandidate(forkAt, {
    id: "f2",
    reading: armReading({ election: { held: true }, arm: { round: 2, label: "C2.1", key: "f2" } }),
  });
  const fork = subtreeCycle(
    { address: forkAt, label: "fork", children: roundsOf(forkAt, [own]) },
    { inherited: [...below.map((c) => c.address), `${C}/r1/gone`] },
  );
  const index = indexLineage(subtreeCampaign({ address: "camp__aaaaaa", children: [cycle(below), fork] }));
  const bars = candidateBars({ ...EMPTY, index, viewedNode: fork });
  // An address the tree does not list draws nothing, never a placeholder bar.
  expect(bars.map((b) => b.key)).toEqual([...below, own].map((c) => c.address));
  expect(bars.map((b) => b.idx)).toEqual([0, 1, 2]);
});

it("reads a held round as its leading arm alone, and a round not yet held whole", () => {
  const led = arm("lead", "C1.2", { election: { held: true, leading: true } });
  const racing = [arm("r1", "C2.1", { election: { held: false } }), arm("r2", "C2.2", { election: { held: false } })];
  const viewedNode = cycle([arm("lost", "C1.1"), led, arm("lost2", "C1.3"), ...racing]);
  const bars = candidateBars({ ...EMPTY, viewedNode, every: false });
  expect(bars.map((b) => b.key)).toEqual([led, ...racing].map((c) => c.address));
  expect(bars.map((b) => b.idx)).toEqual([0, 1, 2]);
  expect(candidateBars({ ...EMPTY, viewedNode })).toHaveLength(5);
});

it("hangs each pass on the arm it measures, by kind, and on no other", () => {
  const mark = (kind: VerificationMark["kind"], key: string): VerificationMark => ({
    kind,
    arm: { key, round: 1, label: "C1.2" },
    state: "running",
    rows: 10,
    scored: 0,
    level: null,
    failed_as: null,
  });
  const bench = mark("bench", "b");
  const verify = mark("verify", "a");
  const bars = candidateBars({
    ...EMPTY,
    viewedNode: cycle([arm("a", "C1.1"), arm("b", "C1.2")]),
    marks: [bench, verify],
  });
  expect(bars.map((b) => b.reading && [b.verify, b.bench])).toEqual([
    [verify, null],
    [null, bench],
  ]);
});
