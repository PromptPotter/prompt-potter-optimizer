"use client";

import type { MeasuredUnit, VerificationMark } from "@/lib/api/types";
import { barLevel, barStarted, type DisplayMetric } from "@/lib/derivations";
import { fmtNum, fmtPct0, unitCount } from "@/lib/format";
import type { CandidateBar } from "@/lib/types";

export type SeriesKey =
  | "accuracy"
  | "ability"
  | "composite"
  | "mask"
  | "overlap"
  | "verify"
  | "bench"
  | "cached";

export interface SeriesCtx {
  metrics: ReadonlySet<DisplayMetric>;
  showMask: boolean;
  showCache: boolean;
  showOverlap: boolean;
  views: readonly CandidateBar[];
  overlapBasis: number | null;
  unit: MeasuredUnit;
  electedMetric: DisplayMetric;
}

export interface SeriesSpec {
  key: SeriesKey;
  metric?: DisplayMetric;
  legend?: (ctx: SeriesCtx) => string;
  hint?: (ctx: SeriesCtx) => string;
  ink: (ctx: SeriesCtx) => string;
  // A mark is a diamond beside its bar's group: a check on it, never a second bar.
  kind: "bar" | "line" | "mark";
  axis: "y" | "y1";
  gap: "floor-when-started" | "sparse";
  signed?: true;
  mark?: { of: (v: CandidateBar) => VerificationMark | null; filled: boolean };
  valueOf: (v: CandidateBar) => number | null;
  applies: (ctx: SeriesCtx) => boolean;
  tip: (v: CandidateBar, ctx: SeriesCtx) => string;
}

export function metricInkToken(m: DisplayMetric, elected: DisplayMetric): string {
  return m === elected ? "--series-elected" : "--series-reading";
}

const metricInk =
  (m: DisplayMetric) =>
  (ctx: SeriesCtx): string =>
    metricInkToken(m, ctx.electedMetric);

const MARK_NAME: Record<VerificationMark["kind"], string> = {
  verify: "verify",
  bench: "held-out bench",
};

const MARK_ROWS: Record<VerificationMark["kind"], string> = {
  verify: " never seen",
  bench: "",
};

const pct = (v: number) => (v * 100).toFixed(0);

function markTip(m: VerificationMark | null, unit: MeasuredUnit): string {
  if (m == null) return "—";
  const name = MARK_NAME[m.kind];
  if (m.failure != null) return `${name}, failed: ${m.failure}`;
  const level = m.level;
  const band =
    level?.ci_lo != null && level.ci_hi != null ? ` [${pct(level.ci_lo)}, ${pct(level.ci_hi)}]` : "";
  if (m.state === "running") {
    const sofar = level == null ? "" : `, ${fmtPct0(level.value)} so far`;
    return `${name}, ${m.scored} of ${unitCount(m.rows, unit)}${MARK_ROWS[m.kind]}${sofar}`;
  }
  const read = level == null ? ", no level" : `, ${fmtPct0(level.value)}${band}`;
  return `${name}, ${unitCount(m.scored, unit)}${MARK_ROWS[m.kind]}${read}`;
}

// A course bar carries no arm, so no pass of this cycle reads it.
const verifyOf = (v: CandidateBar) => (v.reading ? v.verify : null);
const benchOf = (v: CandidateBar) => (v.reading ? v.bench : null);

const markLevel = (m: VerificationMark | null): number | null => m?.level?.value ?? null;

// Array order is draw order.
export const CANDIDATE_SERIES: readonly SeriesSpec[] = [
  {
    key: "accuracy",
    metric: "accuracy",
    ink: metricInk("accuracy"),
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => barLevel("accuracy", v),
    applies: (c) => c.metrics.has("accuracy"),
    tip: (v) => `accuracy: ${fmtNum(barLevel("accuracy", v))}`,
  },
  {
    key: "ability",
    metric: "ability",
    ink: metricInk("ability"),
    kind: "bar",
    axis: "y1",
    // θ is a logit: a floored 0 would be a fabricated middling ability.
    gap: "sparse",
    signed: true,
    valueOf: (v) => barLevel("ability", v),
    applies: (c) => c.metrics.has("ability"),
    tip: (v) => `ability θ: ${fmtNum(barLevel("ability", v), 2)}`,
  },
  {
    key: "composite",
    metric: "composite",
    ink: metricInk("composite"),
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => barLevel("composite", v),
    applies: (c) => c.metrics.has("composite"),
    tip: (v) => `composite: ${fmtNum(barLevel("composite", v))}`,
  },
  {
    key: "mask",
    legend: () => "masked",
    hint: () =>
      "Every score re-read under the criterion you built — the on-disk composite is untouched.",
    ink: () => "--series-counterfactual",
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => (v.reading ? v.node.facts.lens_value : null),
    applies: (c) => c.showMask,
    tip: (v) => `masked: ${fmtNum(v.reading ? v.node.facts.lens_value : null)}`,
  },
  {
    key: "overlap",
    legend: (c) => (c.overlapBasis == null ? "overlap" : `overlap · ${c.overlapBasis}`),
    hint: (c) =>
      `Read on the same ${c.overlapBasis == null ? "cells" : unitCount(c.overlapBasis, c.unit)} — the only bars here that can be differenced against each other, and a candidate that did not answer all of it is blank rather than short. By default the cells C0 and each new best since all answered; pin your own with the set below.`,
    ink: () => "--color-overlap",
    kind: "bar",
    axis: "y",
    gap: "sparse",
    valueOf: (v) => (v.reading ? (v.overlap?.rate ?? null) : null),
    applies: (c) =>
      c.showOverlap && c.views.some((v) => v.reading !== null && v.overlap?.rate != null),
    tip: (v, c) =>
      !v.reading || v.overlap?.rate == null
        ? "overlap: not read on the whole set"
        : `overlap: ${fmtNum(v.overlap.rate)}${v.overlap.n ? ` on ${unitCount(v.overlap.n, c.unit)} shared` : ""}`,
  },
  {
    key: "verify",
    legend: () => "verify",
    hint: () =>
      "An open diamond: this candidate on search cells its rounds never bought, read on those fresh cells alone — the check on the level its bar claims. Picked hardest-first, they sit below that level by construction. It pulses while the pass is out.",
    ink: () => "--color-accent",
    kind: "mark",
    mark: { of: verifyOf, filled: false },
    axis: "y",
    gap: "sparse",
    valueOf: (v) => markLevel(verifyOf(v)),
    applies: (c) => c.views.some((v) => markLevel(verifyOf(v)) != null),
    tip: (v, c) => markTip(verifyOf(v), c.unit),
  },
  {
    key: "bench",
    legend: () => "bench · held out",
    hint: () =>
      "A filled diamond: this candidate on the held-out bench set — questions no round of the search ever read. Graded for the origin as the run starts and for the selection as it ends, so most bars carry none. It pulses while the pass is out.",
    ink: () => "--color-accent",
    kind: "mark",
    mark: { of: benchOf, filled: true },
    axis: "y",
    gap: "sparse",
    valueOf: (v) => markLevel(benchOf(v)),
    applies: (c) => c.views.some((v) => markLevel(benchOf(v)) != null),
    tip: (v, c) => markTip(benchOf(v), c.unit),
  },
  {
    key: "cached",
    legend: () => "share from cache",
    hint: () =>
      "Share of each candidate's scored panel that was replayed from the archive rather than measured.",
    ink: () => "--color-cache",
    kind: "line",
    axis: "y",
    gap: "sparse",
    valueOf: (v) => v.reading?.panel.cached_share ?? null,
    applies: (c) => c.showCache,
    tip: (v, c) => {
      const panel = v.reading?.panel;
      return panel?.cached == null || panel.scored == null
        ? "cached: —"
        : `cached: ${panel.cached} of ${unitCount(panel.scored, c.unit)}`;
    },
  },
];

const BY_KEY = new Map(CANDIDATE_SERIES.map((s) => [s.key as string, s]));

export function seriesByKey(key: string): SeriesSpec | undefined {
  return BY_KEY.get(key);
}

export function activeSeries(ctx: SeriesCtx): SeriesSpec[] {
  return CANDIDATE_SERIES.filter((s) => s.applies(ctx));
}

export function seriesColumn(
  spec: SeriesSpec,
  views: readonly CandidateBar[],
): (number | null)[] {
  return views.map((v) => {
    const raw = spec.valueOf(v);
    if (raw != null) return raw;
    return spec.gap === "floor-when-started" && barStarted(v) ? 0 : null;
  });
}

export interface WhiskerBand {
  anchor: SeriesKey;
  lo: (number | null)[];
  hi: (number | null)[];
}

export function whiskerBands(ctx: SeriesCtx): WhiskerBand[] {
  const bands: WhiskerBand[] = [];
  if (ctx.metrics.has("accuracy")) {
    bands.push({
      anchor: "accuracy",
      lo: ctx.views.map((v) => v.reading?.own?.accuracy?.ci_lo ?? null),
      hi: ctx.views.map((v) => v.reading?.own?.accuracy?.ci_hi ?? null),
    });
  }
  for (const spec of CANDIDATE_SERIES) {
    const mark = spec.mark;
    if (!mark || !spec.applies(ctx)) continue;
    bands.push({
      anchor: spec.key,
      lo: ctx.views.map((v) => mark.of(v)?.level?.ci_lo ?? null),
      hi: ctx.views.map((v) => mark.of(v)?.level?.ci_hi ?? null),
    });
  }
  if (ctx.metrics.has("ability")) {
    // served: domain/ruler.py::theta_band
    bands.push({
      anchor: "ability",
      lo: ctx.views.map((v) => v.reading?.ability?.ci_lo ?? null),
      hi: ctx.views.map((v) => v.reading?.ability?.ci_hi ?? null),
    });
  }
  return bands;
}
