"use client";

import type { MeasuredUnit } from "@/lib/api/types";
import { VERIFY_STRATEGY_LABELS } from "@/lib/api/types.generated";
import { barLevel, type DisplayMetric } from "@/lib/derivations";
import { fmtNum, unitCount } from "@/lib/format";
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
  kind: "bar" | "line";
  axis: "y" | "y1";
  gap: "floor-when-started" | "sparse";
  signed?: true;
  hollow?: true;
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

const benchLevel = (v: CandidateBar): number | null =>
  v.benchPass ? v.benchPass.accuracy : (v.reading?.bench?.level?.value ?? null);

const hasBench = (v: CandidateBar): boolean => v.benchPass != null || v.reading?.bench != null;

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
    valueOf: (v) => v.arm?.lens_value ?? null,
    applies: (c) => c.showMask,
    tip: (v) => `masked: ${fmtNum(v.arm?.lens_value)}`,
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
    valueOf: (v) => v.overlap?.rate ?? null,
    applies: (c) => c.showOverlap && c.views.some((v) => v.overlap?.rate != null),
    tip: (v, c) =>
      v.overlap?.rate == null
        ? "overlap: not read on the whole set"
        : `overlap: ${fmtNum(v.overlap.rate)}${v.overlap.n ? ` on ${unitCount(v.overlap.n, c.unit)} shared` : ""}`,
  },
  {
    key: "verify",
    legend: () => "verify",
    hint: () =>
      "This candidate on search cells its rounds never bought, read on those fresh cells alone — the check on the level its bar claims. Picked hardest-first, they sit below that level by construction.",
    ink: () => "--color-new",
    hollow: true,
    kind: "bar",
    axis: "y",
    gap: "sparse",
    valueOf: (v) => v.reading?.verify?.fresh.accuracy?.value ?? null,
    applies: (c) => c.views.some((v) => v.reading?.verify != null),
    tip: (v, c) => {
      const r = v.reading?.verify;
      if (r == null) return "verify: —";
      return `verify: ${fmtNum(r.fresh.accuracy?.value)} on ${unitCount(r.fresh.n, c.unit)} fresh, ${VERIFY_STRATEGY_LABELS[r.strategy]}`;
    },
  },
  {
    key: "bench",
    legend: () => "bench · held out",
    hint: () =>
      "This candidate on the held-out bench set — questions no round of the search ever read. Graded for the origin as the run starts and for the selection as it ends, so most bars carry none.",
    ink: () => "--color-accent",
    hollow: true,
    kind: "bar",
    axis: "y",
    gap: "sparse",
    valueOf: benchLevel,
    applies: (c) => c.views.some(hasBench),
    tip: (v, c) => {
      const level = benchLevel(v);
      const at = level == null ? "—" : fmtNum(level);
      if (v.benchPass)
        return `bench: ${at} so far · ${v.benchPass.scored} of ${unitCount(v.benchPass.rows, c.unit)} held out`;
      const b = v.reading?.bench;
      return b == null ? "bench: —" : `bench: ${at} on ${unitCount(b.n, c.unit)} held out`;
    },
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
    return spec.gap === "floor-when-started" && v.level != null ? 0 : null;
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
  if (ctx.views.some((v) => v.reading?.verify != null)) {
    bands.push({
      anchor: "verify",
      lo: ctx.views.map((v) => v.reading?.verify?.fresh.accuracy?.ci_lo ?? null),
      hi: ctx.views.map((v) => v.reading?.verify?.fresh.accuracy?.ci_hi ?? null),
    });
  }
  if (ctx.views.some(hasBench)) {
    const edge = (side: "ci_lo" | "ci_hi") => (v: CandidateBar) =>
      v.benchPass ? null : (v.reading?.bench?.level?.[side] ?? null);
    bands.push({
      anchor: "bench",
      lo: ctx.views.map(edge("ci_lo")),
      hi: ctx.views.map(edge("ci_hi")),
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
