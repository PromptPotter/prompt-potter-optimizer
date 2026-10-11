import { describe, expect, it } from "vitest";
import {
  CANDIDATE_SERIES,
  activeSeries,
  seriesColumn,
  whiskerBands,
  type SeriesCtx,
} from "../series";
import type { VerifyReading } from "@/lib/api/types";
import type { CandidateBar } from "@/lib/types";
import { armNode, armReading, ownLevel } from "@/lib/test-fixtures";

function bar(
  reading: Parameters<typeof armReading>[0] = {},
  over: Partial<CandidateBar> = {},
): CandidateBar {
  const node = armNode({ id: "a", label: "C1.1", reading: armReading(reading) }, 1);
  return {
    key: "k",
    idx: 0,
    label: node.label,
    node,
    arm: node,
    reading: node.reading,
    source: "history",
    level: node.reading.own?.accuracy?.value ?? null,
    overlap: null,
    benchPass: null,
    ...over,
  };
}

const ctx = (over: Partial<SeriesCtx> = {}): SeriesCtx => ({
  metrics: new Set(["accuracy", "ability"]),
  showMask: false,
  showCache: false,
  showOverlap: false,
  views: [],
  overlapBasis: null,
  unit: "sample",
  electedMetric: "ability",
  ...over,
});

const spec = (key: string) => CANDIDATE_SERIES.find((s) => s.key === key)!;

describe("a missing value renders as a gap or a floor, never as a measurement", () => {
  const started = [bar({ own: { ...ownLevel(0.5), composite: null } })];

  it("floors the composite and the mask once scoring has begun", () => {
    for (const key of ["composite", "mask"]) {
      expect(seriesColumn(spec(key), started)).toEqual([0]);
    }
    for (const key of ["accuracy", "composite", "mask"]) {
      expect(seriesColumn(spec(key), [bar()])).toEqual([null]);
    }
  });

  it("never floors θ, overlap or verify — not even on a started bar", () => {
    for (const key of ["ability", "overlap", "verify"]) {
      expect(seriesColumn(spec(key), started)).toEqual([null]);
    }
  });

  it("reads its own served number and nothing else", () => {
    const v = bar(
      {
        own: ownLevel(0.7),
        ability: { theta: -1.5, se: 0.2, ci_lo: -1.9, ci_hi: -1.1, caveat: null },
        panel: { cached: 3, scored: 6, cached_share: 0.25 },
      },
      { overlap: { rate: 0.5, n: 4 } },
    );
    expect(seriesColumn(spec("accuracy"), [v])).toEqual([0.7]);
    expect(seriesColumn(spec("ability"), [v])).toEqual([-1.5]);
    expect(seriesColumn(spec("overlap"), [v])).toEqual([0.5]);
    // 0.25, not 3/6: the served share, never the two counts beside it divided here.
    expect(seriesColumn(spec("cached"), [v])).toEqual([0.25]);
    expect(seriesColumn(spec("cached"), [bar()])).toEqual([null]);
    const band = whiskerBands(ctx({ metrics: new Set(["ability"]), views: [v] }));
    expect(band).toEqual([{ anchor: "ability", lo: [-1.9], hi: [-1.1] }]);
  });
});

describe("the axis and sign facts the chart cannot re-derive", () => {
  it("puts exactly θ on its own logit axis, and only θ is signed", () => {
    expect(CANDIDATE_SERIES.filter((s) => s.axis === "y1").map((s) => s.key)).toEqual(["ability"]);
    expect(CANDIDATE_SERIES.filter((s) => s.signed).map((s) => s.key)).toEqual(["ability"]);
  });

  it("keeps provenance a line, drawn last", () => {
    const lines = CANDIDATE_SERIES.filter((s) => s.kind === "line");
    expect(lines.map((s) => s.key)).toEqual(["cached"]);
    expect(CANDIDATE_SERIES.at(-1)?.key).toBe("cached");
  });

  it("gives a chip only to the three metric channels", () => {
    expect(CANDIDATE_SERIES.filter((s) => s.metric).map((s) => s.key)).toEqual([
      "accuracy",
      "ability",
      "composite",
    ]);
    for (const s of CANDIDATE_SERIES) {
      if (!s.metric) expect(s.legend).toBeTypeOf("function");
    }
  });
});

describe("what is on screen", () => {
  it("shows the overlap bars at BOTH on-rungs, and only when a reading exists", () => {
    const withReading = [bar({}, { overlap: { rate: 0.5, n: 4 } })];
    const on = (c: Partial<SeriesCtx>) => activeSeries(ctx(c)).map((s) => s.key);
    expect(on({ showOverlap: true, views: withReading })).toContain("overlap");
    expect(on({ showOverlap: true, views: [bar()] })).not.toContain("overlap");
    expect(on({ showOverlap: false, views: withReading })).not.toContain("overlap");
  });
});

describe("the confidence band", () => {
  it("draws the served band only on the bar that produced it", () => {
    const anchors = (c: Partial<SeriesCtx>) => whiskerBands(ctx(c)).map((b) => b.anchor);
    expect(anchors({ electedMetric: "ability" })).toEqual(["accuracy", "ability"]);
    const both: Partial<SeriesCtx> = {
      electedMetric: "composite",
      metrics: new Set(["accuracy", "composite"]),
    };
    expect(anchors(both)).toEqual(["accuracy"]);
    expect(anchors({ electedMetric: "composite", metrics: new Set(["composite"]) })).toEqual([]);
    expect(anchors({ metrics: new Set(["ability"]) })).toEqual(["ability"]);
  });

  it("puts a verify on its fresh cells alone, under the band those cells produced", () => {
    const verify: VerifyReading = {
      label: "C1.1",
      scorer_id: "s",
      strategy: "random",
      fresh: { accuracy: { value: 0.5, ci_lo: 0.2, ci_hi: 0.8 }, composite: null, n: 6 },
      recorded: { accuracy: { value: 0.75, ci_lo: 0.5, ci_hi: 1 }, composite: null, n: 12 },
      accuracy_increment: -0.25,
      composite_increment: null,
      vs_origin: {
        state: "same_individual",
        a: null,
        b: null,
        cell_set: null,
        instrument_id: null,
        scope: "report",
        spec: { interval_method: "student_t", alpha: 0.05, null_value: 0 },
        coverage: null,
        headline: null,
        beside: [],
      },
      held: true,
      held_absent: null,
    };
    const views = [bar({ own: ownLevel(0.75), verify }), bar({ own: ownLevel(0.6) })];
    expect(seriesColumn(spec("verify"), views)).toEqual([0.5, null]);
    const band = whiskerBands(ctx({ metrics: new Set(), views })).find((b) => b.anchor === "verify");
    expect(band).toEqual({ anchor: "verify", lo: [0.2, null], hi: [0.8, null] });
  });
});
