"use client";
// The bar-chart channels, each declared once (`webapp/CLAUDE.md` § Display-data sources).

import type { MeasuredUnit } from "@/lib/api/types";
import type { HeadlineMetric } from "@/lib/derivations";
import { fmtNum, unitCount } from "@/lib/format";
import type { CandidateView } from "@/lib/types";

export type SeriesKey =
  | "accuracy"
  | "ability"
  | "composite"
  | "mask"
  | "overlap"
  | "verify"
  | "cached";

export interface SeriesCtx {
  metrics: ReadonlySet<HeadlineMetric>;
  showMask: boolean;
  showCache: boolean;
  showOverlap: boolean;
  views: readonly CandidateView[];
  unit: MeasuredUnit;
  electedMetric: HeadlineMetric;
}

export interface SeriesSpec {
  key: SeriesKey;
  metric?: HeadlineMetric;
  // The JOIN to `HEADLINE_METRICS`; its presence also means "this channel has a chip".
  // Chipless channels only.
  legend?: (ctx: SeriesCtx) => string;
  hint?: (ctx: SeriesCtx) => string;
  ink: (ctx: SeriesCtx) => string;
  kind: "bar" | "line";
  axis: "y" | "y1";
  gap: "floor-when-started" | "sparse";
  // Separate from `gap`: a signed series must get no `minBarLength`.
  signed?: true;
  hollow?: true;
  valueOf: (v: CandidateView) => number | null;
  applies: (ctx: SeriesCtx) => boolean;
  tip: (v: CandidateView, ctx: SeriesCtx) => string;
}

export function metricInkToken(m: HeadlineMetric, elected: HeadlineMetric): string {
  return m === elected ? "--series-elected" : "--series-reading";
}

const metricInk =
  (m: HeadlineMetric) =>
  (ctx: SeriesCtx): string =>
    metricInkToken(m, ctx.electedMetric);

function basisN(ctx: SeriesCtx): number {
  let n = 0;
  for (const v of ctx.views) if (v.overlapN != null && v.overlapN > n) n = v.overlapN;
  return n;
}

// Array order is draw order.
export const CANDIDATE_SERIES: readonly SeriesSpec[] = [
  {
    key: "accuracy",
    metric: "accuracy",
    ink: metricInk("accuracy"),
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => v.accuracy,
    applies: (c) => c.metrics.has("accuracy"),
    tip: (v) => `accuracy: ${fmtNum(v.accuracy)}`,
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
    valueOf: (v) => v.theta,
    applies: (c) => c.metrics.has("ability"),
    tip: (v) => `ability θ: ${fmtNum(v.theta, 2)}`,
  },
  {
    key: "composite",
    metric: "composite",
    ink: metricInk("composite"),
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => v.composite,
    applies: (c) => c.metrics.has("composite"),
    tip: (v) => `composite: ${fmtNum(v.composite)}`,
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
    valueOf: (v) => v.lensValue,
    applies: (c) => c.showMask,
    tip: (v) => `masked: ${fmtNum(v.lensValue)}`,
  },
  {
    key: "overlap",
    legend: (c) => `overlap · ${basisN(c)}`,
    hint: (c) =>
      `Read on the same ${unitCount(basisN(c), c.unit)} — the only bars here that can be differenced against each other, and a candidate that did not answer all of it is blank rather than short. By default the cells C0 and every winner since all answered; pin your own with the set below.`,
    ink: () => "--color-overlap",
    kind: "bar",
    axis: "y",
    gap: "sparse",
    valueOf: (v) => v.overlapAccuracy,
    applies: (c) => c.showOverlap && c.views.some((v) => v.overlapAccuracy != null),
    tip: (v, c) =>
      v.overlapAccuracy == null
        ? "overlap: not read on the whole set"
        : `overlap: ${fmtNum(v.overlapAccuracy)}${v.overlapN ? ` on ${unitCount(v.overlapN, c.unit)} shared` : ""}`,
  },
  {
    key: "verify",
    legend: () => "verify",
    hint: () =>
      "A `promptpotter verify` re-run of this candidate over the workspace set — did the verdict hold on more cells?",
    ink: () => "--color-overlap",
    hollow: true,
    kind: "bar",
    axis: "y",
    gap: "sparse",
    valueOf: (v) => v.diag?.accuracy ?? null,
    applies: (c) => c.views.some((v) => v.diag != null),
    tip: (v) =>
      v.diag == null
        ? "verify: —"
        : `verify: ${fmtNum(v.diag.accuracy)} (workspace acc on n=${v.diag.workspaceN}, +${v.diag.samplesAdded} fresh)`,
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
    valueOf: (v) => {
      const n = v.n_samples;
      return v.cached_samples == null || n == null || n <= 0 ? null : v.cached_samples / n;
    },
    applies: (c) => c.showCache,
    // The served integers, never the share: the operator must not READ a number this layer made.
    tip: (v, c) =>
      v.cached_samples == null || v.n_samples == null
        ? "cached: —"
        : `cached: ${v.cached_samples} of ${unitCount(v.n_samples, c.unit)}`,
  },
];

const BY_KEY = new Map(CANDIDATE_SERIES.map((s) => [s.key as string, s]));

export function seriesByKey(key: string): SeriesSpec | undefined {
  return BY_KEY.get(key);
}

export function activeSeries(ctx: SeriesCtx): SeriesSpec[] {
  return CANDIDATE_SERIES.filter((s) => s.applies(ctx));
}

// The floor is a rendering decision and never leaves this array; tooltips read the raw value.
export function seriesColumn(
  spec: SeriesSpec,
  views: readonly CandidateView[],
): (number | null)[] {
  return views.map((v) => {
    const raw = spec.valueOf(v);
    if (raw != null) return raw;
    return spec.gap === "floor-when-started" && v.started ? 0 : null;
  });
}

export function whiskerAnchor(ctx: SeriesCtx): SeriesKey | null {
  if (ctx.electedMetric !== "ability" && ctx.metrics.has(ctx.electedMetric)) {
    const spec = CANDIDATE_SERIES.find((s) => s.metric === ctx.electedMetric);
    if (spec && spec.axis === "y") return spec.key;
  }
  return ctx.metrics.has("accuracy") ? "accuracy" : null;
}

export interface WhiskerBand {
  anchor: SeriesKey;
  lo: (number | null)[];
  hi: (number | null)[];
}

// Widens θ's SE to match the served 95% `mean_fitness_ci`. z (not t) holds only because θ's SE
// is the Rasch posterior SE, not a mean over cells (`shared/statistics.py::mean_ci_t`).
const Z95 = 1.96;

export function whiskerBands(ctx: SeriesCtx): WhiskerBand[] {
  const bands: WhiskerBand[] = [];
  const percent = whiskerAnchor(ctx);
  if (percent !== null) {
    bands.push({
      anchor: percent,
      lo: ctx.views.map((v) => v.meanFitnessCiLo),
      hi: ctx.views.map((v) => v.meanFitnessCiHi),
    });
  }
  if (ctx.metrics.has("ability")) {
    const edge = (sign: number) => (v: CandidateView) =>
      v.theta == null || v.theta_se == null ? null : v.theta + sign * Z95 * v.theta_se;
    bands.push({ anchor: "ability", lo: ctx.views.map(edge(-1)), hi: ctx.views.map(edge(1)) });
  }
  return bands;
}
