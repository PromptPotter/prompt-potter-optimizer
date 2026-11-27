import type {
  RunStanding,
  ServedDashboard,
  ArmName,
  LiveCandidate,
  ServedRound,
} from "@/lib/api/types";
import { describe, expect, it } from "vitest";
import {
  armObserveConfig,
  observeOptions,
  originPoint,
  resolveObserveSubject,
  type ObserveSelection,
  type ObserveSubject,
} from "../search-point";
import { servedArm } from "../candidate-search-point";
import {
  armReading,
  currentRound,
  dash,
  hierarchyCandidate,
  liveRow,
  ownLevel,
  pairedReading,
  roundDoc,
  roundOfArms,
  runStanding,
  servedLabel,
  summaryCandidate,
  summaryRound,
} from "@/lib/test-fixtures";

const live = (
  round: number,
  label: string,
  over: Partial<LiveCandidate> = {},
  reading: Parameters<typeof armReading>[0] = {},
): LiveCandidate =>
  liveRow({ reading: armReading({ ...reading, arm: { round, label, key: label } }), ...over });

// One prompt node's instruction, as `OptSearchPoint.prompts` serves it.
const p = (instruction: string, node = "llm_only") => ({ [node]: { fields: { instruction }, shots: [] } });

const liveDash = (candidates: (Partial<LiveCandidate> & { label: string })[]): ServedDashboard =>
  dash({
    current_round: currentRound({
      round: 1,
      candidates: candidates.map(({ label, ...over }) => live(1, label, over)),
    }),
  });

describe("armObserveConfig", () => {
  const snap = liveDash([
    { label: "C1.1", prompts: p("a"), resolved_params: { llm: { model: "x" } } },
    { label: "C1.2", prompts: p("b"), resolved_params: { llm: { model: "y" } } },
  ]);
  const inFlight = snap.current_round.candidates;
  const doc = roundOfArms(2, [
    { key: "cand-a", label: "C2.1", prompts: p("a"), resolved_params: { llm: { model: "ma" } } },
    { key: "cand-b", label: "C2.2", prompts: p("b"), resolved_params: { llm: { model: "mb" } } },
  ]);

  it("projects an arm in flight and a filed one alike, off its key", () => {
    const r = armObserveConfig(servedArm(inFlight, "C1.1"), "selected · C1.1");
    expect(r?.label).toBe("selected · C1.1");
    expect(r?.prompts).toEqual(p("a"));
    expect(r?.config).toEqual({ llm: { model: "x" } });
    const f = armObserveConfig(servedArm(doc.candidates, "cand-b"), "winner · C2.2");
    expect(f?.config).toEqual({ llm: { model: "mb" } });
    expect(f?.prompts).toEqual(p("b"));
  });

  it("a node reads the prompt IT runs; the whole searchpoint reads every node's", () => {
    const both = { ...p("own", "solve"), ...p("node", "l1_generate") };
    const outer = roundOfArms(1, [{ key: "c", label: "C1.1", prompts: both }]);
    const prompts = (node?: string) =>
      armObserveConfig(servedArm(outer.candidates, "c"), "x", node)?.prompts;
    expect(prompts("l1_generate")).toEqual(p("node", "l1_generate"));
    expect(prompts("l2_analyze")).toEqual({});
    expect(prompts()).toEqual(both);
  });

  it("returns null for a missing arm", () => {
    expect(armObserveConfig(servedArm(inFlight, "C1.10"), "x")).toBeNull();
    expect(armObserveConfig(servedArm(doc.candidates, "C9.9"), "x")).toBeNull();
    expect(armObserveConfig(servedArm(roundDoc({ round: 1 }).candidates, "C2.1"), "x")).toBeNull();
  });
});

// `winnerIdx` -1 is a round that held; it still serves the arm its reading is taken off.
const crowned = (
  round: number,
  winnerIdx: number,
  n: number,
  over: Partial<ServedRound> = {},
  leadingIdx = winnerIdx,
) => {
  const arm = (i: number): ArmName => ({
    round,
    label: servedLabel(round, i),
    key: `r${round}c${i}`,
  });
  return summaryRound({
    round,
    leading: leadingIdx >= 0 ? arm(leadingIdx) : null,
    ending: round === 0 ? null : winnerIdx >= 0 ? "crowned" : "held",
    candidates: Array.from({ length: n }, (_, i) =>
      summaryCandidate({
        reading: armReading({
          arm: arm(i),
          own: ownLevel((round * 10 + i) / 100),
          election: { held: true, selected: i === winnerIdx, leading: i === leadingIdx },
        }),
      }),
    ),
    ...over,
  });
};

// The served selection names the crowned row's own key.
const standing = (round: number, i: number) => {
  const label = servedLabel(round, i);
  return runStanding(round, label, { selection: { round, label, key: `r${round}c${i}` } });
};

const NO_PICK: ObserveSelection = { round: null, observe: null };
const asked = (observe: ObserveSelection["observe"]): ObserveSelection => ({ ...NO_PICK, observe });

describe("resolveObserveSubject — best", () => {
  const best = (snap: ServedDashboard | null, stood: RunStanding | null) =>
    resolveObserveSubject(snap, stood, asked("best"));

  it("takes the served winner, not the newest crown it could find itself", () => {
    const s = best(
      dash({ rounds: [crowned(0, 0, 1), crowned(1, 2, 3), crowned(2, 1, 3)] }),
      standing(1, 2),
    );
    expect(s.state).toBe("best");
    expect(s.point).toMatchObject({ round: 1, label: "C1.3" });
    expect(s.point?.key).toBe("r1c2");
  });

  it("badges the origin as `origin`, a promoted winner as `best`", () => {
    const rounds = [crowned(0, 0, 1), crowned(1, 0, 3)];
    expect(best(dash({ rounds }), runStanding(0, "C0")).point?.title).toBe("origin · C0");
    expect(best(dash({ rounds }), runStanding(1, "C1.1")).point?.title).toBe("best · C1.1");
  });

  it("has no point before anything is measured", () => {
    for (const snap of [null, dash({}), dash({ rounds: [summaryRound({ round: 0 })] })]) {
      const s = resolveObserveSubject(snap, null, NO_PICK);
      expect(s.point).toBeNull();
      expect(s.reading).toBeNull();
      expect(s.round).toBeNull();
      expect(s.options).toEqual([]);
    }
  });
});

describe("resolveObserveSubject — every state resolves its row by arm key", () => {
  const rounds = [
    crowned(0, 0, 1),
    crowned(1, 1, 3),
    crowned(2, -1, 3, { verdict_reason: "no arm cleared the parent" }, 1),
  ];
  // The cycle entry's standing: `CycleEntryFacts.standing`.
  const stood = standing(1, 1);
  // `observed` is served: `served_dashboard.py::_observed`
  const closed = dash({
    rounds,
    observed: { round: 2, label: "C2.3", key: "r2c2" },
    round_axis: { completed: [0, 1, 2], live: null, position: 2, unfiled: null },
  });
  const running = dash({
    rounds,
    observed: { round: 3, label: "C3.2", key: "C3.2" },
    round_axis: { completed: [0, 1, 2], live: 3, position: 3, unfiled: 3 },
    current_round: currentRound({
      round: 3,
      candidates: [
        live(3, "C3.1", {}, { own: ownLevel(0.5) }),
        live(
          3,
          "C3.2",
          {},
          {
            own: ownLevel(0.75),
            vs_reference: pairedReading(0.15, [0.02, 0.28], { rateA: 0.6 }),
          },
        ),
      ],
    }),
  });
  // The point names its arm; the round read (`useRound`) is the one source of its reading.
  const keyOf = (s: ObserveSubject) => s.point?.key;

  it("best: the last crown, with ITS round's reading while a later round held", () => {
    const s = resolveObserveSubject(closed, stood, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.point).toMatchObject({ round: 1, label: "C1.2", title: "best · C1.2" });
    expect(keyOf(s)).toBe("r1c1");
    expect(s.reading).toMatchObject({ round: 1, ending: "crowned" });
    expect(s.verdict).toMatchObject({ round: 2, ending: "held" });
    expect(s.round).toBe(1);
    expect(s.live).toBe(false);
  });

  it("latest at rest: the served observed arm, and that round's verdict", () => {
    const s = resolveObserveSubject(closed, stood, asked("latest"));
    expect(s.point).toMatchObject({ round: 2, label: "C2.3", title: "latest · C2.3" });
    expect(keyOf(s)).toBe("r2c2");
    expect(s.reading).toMatchObject({ round: 2, ending: "held" });
    expect(s.verdict).toBeNull();
  });

  it("latest while live: the in-flight arm, and no closed reading", () => {
    const s = resolveObserveSubject(running, stood, NO_PICK);
    expect(s.state).toBe("latest");
    expect(s.point).toMatchObject({ round: 3, label: "C3.2", title: "live — C3.2" });
    expect(keyOf(s)).toBe("C3.2");
    expect(s.reading).toBeNull();
    expect(s.round).toBe(3);
    expect(s.live).toBe(true);
  });

  it("latest while live: whichever arm the server observes, never one picked here", () => {
    const measuring = dash({
      ...running,
      observed: { round: 3, label: "C3.1", key: "C3.1" },
    });
    expect(resolveObserveSubject(measuring, stood, NO_PICK).point).toMatchObject({
      round: 3,
      label: "C3.1",
      title: "live — C3.1",
    });
  });

  it("round:the arm the picked round's reading is taken off — its crown, else the served leader", () => {
    const one = resolveObserveSubject(closed, stood, { ...NO_PICK, round: 1 });
    expect(one.state).toBe("round");
    expect(one.point).toMatchObject({ round: 1, label: "C1.2", title: "round 1 · C1.2" });
    expect(keyOf(one)).toBe("r1c1");
    const held = resolveObserveSubject(closed, stood, { ...NO_PICK, round: 2 });
    expect(held.point).toMatchObject({ round: 2, label: "C2.2" });
    expect(keyOf(held)).toBe("r2c1");
    expect(held.reading?.round).toBe(2);
    expect(held.verdict).toBe(held.reading);
    const zero = resolveObserveSubject(closed, stood, { ...NO_PICK, round: 0 });
    expect(zero.point).toMatchObject({ round: 0, label: "C0", title: "round 0 · C0" });
    expect(keyOf(zero)).toBe("r0c0");
  });

  it("an asked-for state wins while it has a target, and is dropped when it has none", () => {
    expect(resolveObserveSubject(running, stood, asked("best")).point?.label).toBe("C1.2");
    expect(resolveObserveSubject(closed, stood, asked("round")).state).toBe("best");
    expect(
      resolveObserveSubject(closed, stood, { ...NO_PICK, round: 2, observe: "best" }).point,
    ).toMatchObject({ round: 1, label: "C1.2" });
  });

  it("while the live round has seeded nothing, shows the parent and keeps the round on the live one", () => {
    const generating = dash({
      ...closed,
      current_round: currentRound({ round: 3 }),
      round_axis: { completed: [0, 1, 2], live: 3, position: 3, unfiled: 3 },
    });
    const s = resolveObserveSubject(generating, stood, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.point?.round).toBe(1);
    expect(s.reading?.round).toBe(1);
    expect(s.round).toBe(3);
    expect(s.live).toBe(true);
    expect(resolveObserveSubject(generating, stood, asked("best")).round).toBe(1);
  });

  it("ignores `current_round` rows that linger after a stop: served `round_axis.live` is null", () => {
    const stopped = dash({
      ...running,
      round_axis: { completed: [0, 1, 2], live: null, position: 2, unfiled: 3 },
    });
    const s = resolveObserveSubject(stopped, stood, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.live).toBe(false);
  });
});

describe("originPoint — the origin as the served tree names it", () => {
  it("addresses the origin by the arm key its own cycle speaks", () => {
    const p = originPoint(hierarchyCandidate("camp::cycle_a", { id: "r0c0", label: "C0" }));
    expect(p).toMatchObject({ round: 0, key: "r0c0", label: "C0", title: "origin · C0" });
  });

  it("is null until the tree names one — never round 0's first row", () => {
    expect(originPoint(null)).toBeNull();
  });
});

describe("observeOptions", () => {
  it("DROPS unavailable states rather than disabling them, and keeps one order", () => {
    const none = { best: false, latest: false, round: false };
    expect(observeOptions({ ...none, best: true, latest: true })).toEqual([
      { value: "best", label: "Best" },
      { value: "latest", label: "Most recent" },
    ]);
    expect(observeOptions({ ...none, latest: true, round: true }, 4)).toEqual([
      { value: "latest", label: "Most recent" },
      { value: "round", label: "Round 4" },
    ]);
    expect(observeOptions(none)).toEqual([]);
  });
});
