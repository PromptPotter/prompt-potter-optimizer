import { describe, expect, it } from "vitest";
import {
  CANDIDATE_SERIES,
  activeSeries,
  seriesColumn,
  whiskerAnchor,
  type SeriesCtx,
} from "../series";
import type { CandidateView } from "@/lib/types";

// Every one of these fails silently on screen: a plausible bar, no whisker, a `transparent` ink.

function view(over: Partial<CandidateView>): CandidateView {
  return {
    key: "k",
    round: 1,
    idx: 0,
    candidate_id: "a",
    label: "C1.1",
    accuracy: null,
    composite: null,
    theta: null,
    theta_se: null,
  thetaCaveat: null,
    meanFitnessCiLo: null,
    meanFitnessCiHi: null,
    matchedParentLift: null,
    matchedParentLiftCiLo: null,
    matchedParentLiftCiHi: null,
    evaluators: {},
    is_winner: false,
    n_samples: null,
    n_expected: null,
    cached_samples: null,
    source: "history",
    lensValue: null,
    compositeRank: null,
    lensRank: null,
    started: false,
    electionPending: false,
    overlapAccuracy: null,
    overlapN: null,
    ...over,
  };
}

const ctx = (over: Partial<SeriesCtx> = {}): SeriesCtx => ({
  metrics: new Set(["accuracy", "ability"]),
  showMask: false,
  showCache: false,
  showOverlap: false,
  views: [],
  unit: "sample",
  electedMetric: "ability",
  ...over,
});

const spec = (key: string) => CANDIDATE_SERIES.find((s) => s.key === key)!;

describe("a missing value renders as a gap or a floor, never as a measurement", () => {
  it("floors accuracy, composite and the mask once scoring has begun", () => {
    const started = [view({ started: true })];
    for (const key of ["accuracy", "composite", "mask"]) {
      expect(seriesColumn(spec(key), started)).toEqual([0]);
      expect(seriesColumn(spec(key), [view({ started: false })])).toEqual([null]);
    }
  });

  it("never floors θ, overlap or verify — not even on a started bar", () => {
    const started = [view({ started: true })];
    for (const key of ["ability", "overlap", "verify"]) {
      expect(seriesColumn(spec(key), started)).toEqual([null]);
    }
  });

  it("reads its own served number and nothing else", () => {
    const v = view({ accuracy: 0.7, theta: -1.5, overlapAccuracy: 0.5, cached_samples: 3, n_samples: 6 });
    expect(seriesColumn(spec("ability"), [v])).toEqual([-1.5]);
    expect(seriesColumn(spec("overlap"), [v])).toEqual([0.5]);
    expect(seriesColumn(spec("cached"), [v])).toEqual([0.5]);
    expect(seriesColumn(spec("cached"), [view({ cached_samples: 0, n_samples: 0 })])).toEqual([null]);
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
    // Everything else must carry its own legend text, or it appears unlabelled.
    for (const s of CANDIDATE_SERIES) {
      if (!s.metric) expect(s.legend).toBeTypeOf("function");
    }
  });
});

describe("what is on screen", () => {
  it("shows the overlap bars at BOTH on-rungs, and only when a reading exists", () => {
    const withReading = [view({ overlapAccuracy: 0.5 })];
    const on = (c: Partial<SeriesCtx>) => activeSeries(ctx(c)).map((s) => s.key);
    // `showOverlap` is `rung > 0`: on for both the served set and a picked one.
    expect(on({ showOverlap: true, views: withReading })).toContain("overlap");
    // On, but nothing has been read on the whole set yet.
    expect(on({ showOverlap: true, views: [view({})] })).not.toContain("overlap");
    // A reading exists but the operator stepped the control off.
    expect(on({ showOverlap: false, views: withReading })).not.toContain("overlap");
  });
});

describe("the confidence band", () => {
  it("anchors on a percent-axis bar, and reports NOTHING rather than guessing", () => {
    expect(whiskerAnchor(ctx({ electedMetric: "ability" }))).toBe("accuracy");
    expect(whiskerAnchor(ctx({ electedMetric: "composite", metrics: new Set(["composite"]) })))
      .toBe("composite");
    // θ alone on screen: no percent bar exists, so the band must not be drawn.
    expect(whiskerAnchor(ctx({ metrics: new Set(["ability"]) }))).toBeNull();
  });
});
