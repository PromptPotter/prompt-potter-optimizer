import { describe, expect, it } from "vitest";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  liveObserveConfig,
  observeOptions,
  originPoint,
  resolveObserveSubject,
  type ObserveSelection,
  type ObserveSubject,
} from "../searchPoint";
import type { DashboardSnapshot } from "@/lib/poll";
import type { ArmPointer, LiveCandidate, ServedRound } from "@/lib/api/types";
import {
  armNode,
  armReading,
  currentRound,
  dash,
  liveRow,
  ownLevel,
  pairedReading,
  roundDoc,
  runStanding,
  scored,
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
  liveRow({ reading: armReading({ ...reading, arm: { round, label } }), ...over });

const liveDash = (candidates: (Partial<LiveCandidate> & { label: string })[]): DashboardSnapshot =>
  dash({
    current_round: currentRound({
      round: 1,
      candidates: candidates.map(({ label, ...over }) => live(1, label, over)),
    }),
  });

describe("liveObserveConfig", () => {
  it("returns null with no live candidates", () => {
    expect(liveObserveConfig(null)).toBeNull();
    expect(liveObserveConfig(liveDash([]))).toBeNull();
  });

  it("picks the latest-seeded candidate — the last served row — and its resolved config", () => {
    const r = liveObserveConfig(
      liveDash([
        { label: "C1.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "x" } } },
        { label: "C1.2", prompt_fields: { instruction: "b" } },
        { label: "C1.3", prompt_fields: { instruction: "c" }, resolved_pipeline_params: { llm: { model: "z" } } },
      ]),
    );
    expect(r?.label).toBe("live — C1.3");
    expect(r?.promptFields).toEqual({ instruction: "c" });
    expect(r?.config).toEqual({ llm: { model: "z" } });
  });

  it("defaults config to {} when the candidate carries none yet", () => {
    const r = liveObserveConfig(liveDash([{ label: "C1.1", prompt_fields: { instruction: "a" } }]));
    expect(r?.config).toEqual({});
  });
});

describe("liveCandidateObserveConfig", () => {
  const snap = liveDash([
    { label: "C1.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "x" } } },
    { label: "C1.2", prompt_fields: { instruction: "b" }, resolved_pipeline_params: { llm: { model: "y" } } },
  ]);

  it("locates the in-flight candidate by label (not the latest-seeded one)", () => {
    const r = liveCandidateObserveConfig(snap, "C1.1");
    expect(r?.label).toBe("live — C1.1");
    expect(r?.promptFields).toEqual({ instruction: "a" });
    expect(r?.config).toEqual({ llm: { model: "x" } });
  });

  it("returns null for an unknown / not-yet-seeded label", () => {
    expect(liveCandidateObserveConfig(snap, "C1.10")).toBeNull();
    expect(liveCandidateObserveConfig(snap, "")).toBeNull();
  });

  it("returns null without a live round", () => {
    expect(liveCandidateObserveConfig(null, "C1.1")).toBeNull();
    expect(liveCandidateObserveConfig(dash({}), "C1.1")).toBeNull();
  });
});

// A resume re-scores the origin under a new lineage id, while `round_0000.json` keeps the first run's.
describe("the origin as an ordinary candidate", () => {
  const round0 = roundDoc({
    round: 0,
    candidate_scores: [
      scored({ candidate_id: "c0", label: "C0", prompt_fields: { instruction: "o" }, resolved_pipeline_params: { llm: { model: "m0" } } }),
    ],
  });

  it("resolves C0 out of round 0 by its positional label", () => {
    const r = candidateObserveConfig(round0, "C0", "selected · C0");
    expect(r?.label).toBe("selected · C0");
    expect(r?.config).toEqual({ llm: { model: "m0" } });
    expect(r?.promptFields).toEqual({ instruction: "o" });
  });

  it("still resolves when the id on disk is from an earlier run", () => {
    const remintedIdIsIrrelevant = candidateObserveConfig(round0, "C0", "selected · C0");
    expect(remintedIdIsIrrelevant?.config).toEqual({ llm: { model: "m0" } });
  });

  it("returns null before round 0 is written", () => {
    expect(candidateObserveConfig(null, "C0", "C0")).toBeNull();
    expect(candidateObserveConfig(roundDoc({ round: 0 }), "C0", "C0")).toBeNull();
  });
});

describe("candidateObserveConfig", () => {
  const doc = roundDoc({
    candidate_scores: [
      scored({ candidate_id: "cand-a", label: "C2.1", prompt_fields: { instruction: "a" }, resolved_pipeline_params: { llm: { model: "ma" } } }),
      scored({ candidate_id: "cand-b", label: "C2.2", prompt_fields: { instruction: "b" }, resolved_pipeline_params: { llm: { model: "mb" } } }),
    ],
  });

  it("locates a candidate by position and projects its resolved config", () => {
    const r = candidateObserveConfig(doc, "C2.2", "winner · C2.2");
    expect(r?.label).toBe("winner · C2.2");
    expect(r?.config).toEqual({ llm: { model: "mb" } });
    expect(r?.promptFields).toEqual({ instruction: "b" });
  });

  it("returns null for a missing label / doc", () => {
    expect(candidateObserveConfig(doc, "C9.9", "x")).toBeNull();
    expect(candidateObserveConfig(null, "C2.1", "x")).toBeNull();
    expect(candidateObserveConfig(roundDoc({ round: 1 }), "C2.1", "x")).toBeNull();
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
  const arm = (i: number): ArmPointer => ({
    round,
    label: servedLabel(round, i),
    candidate_id: `r${round}c${i}`,
  });
  return summaryRound({
    round,
    leading: leadingIdx >= 0 ? arm(leadingIdx) : null,
    selected: winnerIdx >= 0 ? [arm(winnerIdx)] : [],
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

const NO_PICK: ObserveSelection = { candidate: null, round: null, observe: null };
const asked = (observe: ObserveSelection["observe"]): ObserveSelection => ({ ...NO_PICK, observe });

describe("resolveObserveSubject — best", () => {
  const best = (snap: DashboardSnapshot | null) => resolveObserveSubject(snap, false, asked("best"));

  it("takes the served winner, not the newest crown it could find itself", () => {
    const s = best(
      dash({
        rounds: [crowned(0, 0, 1), crowned(1, 2, 3), crowned(2, 1, 3)],
        run_standing: runStanding(1, "C1.3"),
      }),
    );
    expect(s.state).toBe("best");
    expect(s.point).toMatchObject({ round: 1, label: "C1.3" });
    expect(s.point?.row?.reading.arm.candidate_id).toBe("r1c2");
  });

  it("badges the origin as `origin`, a promoted winner as `best`", () => {
    const rounds = [crowned(0, 0, 1), crowned(1, 0, 3)];
    expect(best(dash({ rounds, run_standing: runStanding(0, "C0") })).point?.title).toBe("origin · C0");
    expect(best(dash({ rounds, run_standing: runStanding(1, "C1.1") })).point?.title).toBe(
      "best · C1.1",
    );
  });

  it("has no point before anything is measured", () => {
    for (const snap of [null, dash({}), dash({ rounds: [summaryRound({ round: 0 })] })]) {
      const s = resolveObserveSubject(snap, false, NO_PICK);
      expect(s.point).toBeNull();
      expect(s.reading).toBeNull();
      expect(s.round).toBeNull();
      expect(s.options).toEqual([]);
    }
  });
});

describe("resolveObserveSubject — every state resolves its row by label", () => {
  const rounds = [
    crowned(0, 0, 1),
    crowned(1, 1, 3, { improved: true }),
    crowned(2, -1, 3, { improved: false, verdict_reason: "no arm cleared the parent" }, 1),
  ];
  const run_standing = runStanding(1, "C1.2");
  // `observed` is served: `served_dashboard.py::_observed`
  const closed = dash({
    rounds,
    run_standing,
    observed: { round: 2, label: "C2.3", candidate_id: "r2c2" },
    round_axis: { completed: [0, 1, 2], live: null, position: 2 },
  });
  const running = dash({
    rounds,
    run_standing,
    observed: { round: 3, label: "C3.2", candidate_id: "" },
    round_axis: { completed: [0, 1, 2], live: 3, position: 3 },
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
  const joined = (s: ObserveSubject) => {
    const arm = s.point?.row?.reading.arm;
    return arm !== undefined && arm.round === s.point?.round && arm.label === s.point?.label;
  };
  const level = (s: ObserveSubject) => s.point?.row?.reading.own?.accuracy?.value;

  it("best: the last crown, with ITS round's reading while a later round held", () => {
    const s = resolveObserveSubject(closed, false, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.point).toMatchObject({ round: 1, label: "C1.2", title: "best · C1.2" });
    expect(joined(s)).toBe(true);
    expect(s.point?.row?.reading.election.selected).toBe(true);
    expect(s.reading).toMatchObject({ round: 1, improved: true });
    expect(s.verdict).toMatchObject({ round: 2, improved: false });
    expect(s.round).toBe(1);
    expect(s.live).toBe(false);
  });

  it("latest at rest: the served observed arm, and that round's verdict", () => {
    const s = resolveObserveSubject(closed, false, asked("latest"));
    expect(s.point).toMatchObject({ round: 2, label: "C2.3", title: "latest · C2.3" });
    expect(joined(s)).toBe(true);
    expect(s.reading).toMatchObject({ round: 2, improved: false });
    expect(s.verdict).toBeNull();
  });

  it("latest while live: the in-flight row, its accuracy in hand, and no closed reading", () => {
    const s = resolveObserveSubject(running, true, NO_PICK);
    expect(s.state).toBe("latest");
    expect(s.point).toMatchObject({ round: 3, label: "C3.2", title: "live — C3.2" });
    expect(joined(s)).toBe(true);
    expect(s.point?.row?.source).toBe("inflight");
    expect(level(s)).toBe(0.75);
    expect(s.reading).toBeNull();
    expect(s.round).toBe(3);
    expect(s.live).toBe(true);
  });

  it("latest while live: whichever arm the server observes, never one picked here", () => {
    const measuring = dash({
      ...running,
      observed: { round: 3, label: "C3.1", candidate_id: "" },
    });
    expect(resolveObserveSubject(measuring, true, NO_PICK).point).toMatchObject({
      round: 3,
      label: "C3.1",
      title: "live — C3.1",
    });
  });

  it("round:the arm the picked round's reading is taken off — its crown, else the served leader", () => {
    const one = resolveObserveSubject(closed, false, { ...NO_PICK, round: 1 });
    expect(one.state).toBe("round");
    expect(one.point).toMatchObject({ round: 1, label: "C1.2", title: "round 1 · C1.2" });
    expect(joined(one)).toBe(true);
    const held = resolveObserveSubject(closed, false, { ...NO_PICK, round: 2 });
    expect(held.point).toMatchObject({ round: 2, label: "C2.2" });
    expect(joined(held)).toBe(true);
    expect(held.reading?.round).toBe(2);
    expect(held.verdict).toBe(held.reading);
    const zero = resolveObserveSubject(closed, false, { ...NO_PICK, round: 0 });
    expect(zero.point).toMatchObject({ round: 0, label: "C0", title: "round 0 · C0" });
    expect(joined(zero)).toBe(true);
  });

  it("selected: a closed pick and an in-flight pick both find their row", () => {
    const pick = (snap: DashboardSnapshot, live: boolean, round: number, label: string) =>
      resolveObserveSubject(snap, live, { candidate: { round, label }, round, observe: null });
    const past = pick(closed, false, 1, "C1.1");
    expect(past.state).toBe("selected");
    expect(past.point).toMatchObject({ round: 1, label: "C1.1", title: "selected · C1.1" });
    expect(past.point?.row?.reading.arm.candidate_id).toBe("r1c0");
    expect(past.reading?.round).toBe(1);
    const now = pick(running, true, 3, "C3.1");
    expect(now.point?.row?.source).toBe("inflight");
    expect(joined(now)).toBe(true);
    expect(level(now)).toBe(0.5);
    expect(now.live).toBe(true);
  });

  it("an asked-for state wins while it has a target, and is dropped when it has none", () => {
    expect(resolveObserveSubject(running, true, asked("best")).point?.label).toBe("C1.2");
    expect(resolveObserveSubject(closed, false, asked("selected")).state).toBe("best");
    expect(
      resolveObserveSubject(closed, false, { ...NO_PICK, round: 2, observe: "best" }).point,
    ).toMatchObject({ round: 1, label: "C1.2" });
  });

  it("while the live round has seeded nothing, shows the parent and keeps the round on the live one", () => {
    const generating = dash({
      ...closed,
      current_round: currentRound({ round: 3 }),
      round_axis: { completed: [0, 1, 2], live: 3, position: 3 },
    });
    const s = resolveObserveSubject(generating, true, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.point?.round).toBe(1);
    expect(s.reading?.round).toBe(1);
    expect(s.round).toBe(3);
    expect(s.live).toBe(true);
    expect(resolveObserveSubject(generating, true, asked("best")).round).toBe(1);
  });

  it("ignores `current_round` rows that linger after a stop", () => {
    const s = resolveObserveSubject(running, false, NO_PICK);
    expect(s.state).toBe("best");
    expect(s.live).toBe(false);
  });
});

describe("originPoint — the origin as the served tree names it", () => {
  const snap = dash({ rounds: [crowned(0, 0, 1), crowned(1, 0, 2)] });

  it("addresses the origin by the arm key its own course speaks", () => {
    const p = originPoint(snap, armNode({ id: "r0c0", label: "C0" }));
    expect(p).toMatchObject({ round: 0, label: "C0", title: "origin · C0" });
    expect(p?.row?.reading.arm.candidate_id).toBe("r0c0");
  });

  it("is null until the tree names one — never round 0's first row", () => {
    expect(originPoint(snap, null)).toBeNull();
  });
});

describe("observeOptions", () => {
  it("DROPS unavailable states rather than disabling them, and keeps one order", () => {
    const none = { best: false, latest: false, round: false, selected: false };
    expect(observeOptions({ ...none, best: true, latest: true })).toEqual([
      { value: "best", label: "Best" },
      { value: "latest", label: "Most recent" },
    ]);
    expect(observeOptions({ ...none, latest: true, round: true, selected: true }, 4)).toEqual([
      { value: "latest", label: "Most recent" },
      { value: "round", label: "Round 4" },
      { value: "selected", label: "Selected" },
    ]);
    expect(observeOptions(none)).toEqual([]);
  });
});
