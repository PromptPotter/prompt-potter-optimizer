"use client";
// THE bar-chart channels, declared once each. The legend, the datasets, the columns, the
// tooltip and the plugin joins all read this table; adding a channel is one entry.
//
// Two fields carry facts nothing else states:
//   • `gap` and `signed` are separate. `verify` is sparse and still wants a minimum bar
//     length; θ is sparse AND signed, so it must not have one — chart.js applies
//     `minBarLength` as an absolute length and paints a negative logit's stub below zero.
//   • `metric` is the JOIN to `HEADLINE_METRICS`, which owns the glyph, prose and order. It
//     also answers "does this channel have a chip", which is why the legend keeps no list.

import type { MeasuredUnit } from "@/lib/api/types";
import type { HeadlineMetric } from "@/lib/derivations";
import { fmtNum, unitCount } from "@/lib/format";
import type { CandidateView } from "@/lib/types";

export type SeriesKey =
  | "accuracy"
  | "ability"
  | "composite"
  | "mask"
  | "trajectory"
  | "verify"
  | "cached";

export interface SeriesCtx {
  metrics: ReadonlySet<HeadlineMetric>;
  showMask: boolean;
  showCache: boolean;
  showTrajectory: boolean;
  views: readonly CandidateView[];
  unit: MeasuredUnit;
  // Served `dash.headline_metric` — the one input deciding which channel reads primary.
  electedMetric: HeadlineMetric;
}

export interface SeriesSpec {
  key: SeriesKey;
  metric?: HeadlineMetric;
  // Chipless channels only — a metric restating `headlineMetricLabel` under its own chip is
  // the duplication this table deletes.
  legend?: (ctx: SeriesCtx) => string;
  hint?: (ctx: SeriesCtx) => string;
  // ONE name: the legend swatch resolves it with `var()`, the canvas with `getCss()`.
  ink: (ctx: SeriesCtx) => string;
  kind: "bar" | "line";
  // `y1` is θ's logit axis, declared only while θ shows — the right gutter feeds the
  // dendrogram's alignment, so an axis reserving space for absent data slides every node off
  // its bar.
  axis: "y" | "y1";
  // `floor-when-started` paints a stub once scoring begins, telling "still computing" apart
  // from "not yet started".
  gap: "floor-when-started" | "sparse";
  signed?: true;
  // Outline, no fill — corroboration of a bar rather than a rival beside it, spending no new
  // hue to say so.
  hollow?: true;
  // This channel's own SERVED number. Computes nothing.
  valueOf: (v: CandidateView) => number | null;
  applies: (ctx: SeriesCtx) => boolean;
  tip: (v: CandidateView, ctx: SeriesCtx) => string;
}

// Per CAMPAIGN: the metric the engine elects on reads at full accent, its siblings recede
// into the same hue. Three steps is the ceiling — at 25 bars × 3 series a bar is ~6px.
export function metricInkToken(m: HeadlineMetric, elected: HeadlineMetric): string {
  return m === elected ? "--series-elected" : "--series-reading";
}

const metricInk =
  (m: HeadlineMetric) =>
  (ctx: SeriesCtx): string =>
    metricInkToken(m, ctx.electedMetric);

// Every token the table can resolve to, so the stylesheet check needs no campaign.
export const SERIES_INK_TOKENS = [
  "--series-elected",
  "--series-reading",
  "--series-counterfactual",
  "--color-overlap",
  "--color-cache",
] as const;

// The set drifts as the adopted line grows, so the legend reads its size off the data.
function overlapN(ctx: SeriesCtx): number {
  let n = 0;
  for (const v of ctx.views) if (v.overlapN != null && v.overlapN > n) n = v.overlapN;
  return n;
}

// Draw order. Bars in metric-axis order; the provenance line last, since it paints OVER the
// group rather than beside it.
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
    // θ is a LOGIT: 0 is a real, middling ability, so a floored θ is a fabricated
    // measurement rather than an empty slot.
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
    // A criterion like the three above, just a counterfactual one — its own step in the
    // accent family rather than a rival hue.
    ink: () => "--series-counterfactual",
    kind: "bar",
    axis: "y",
    gap: "floor-when-started",
    valueOf: (v) => v.lensValue,
    applies: (c) => c.showMask,
    tip: (v) => `masked: ${fmtNum(v.lensValue)}`,
  },
  {
    key: "trajectory",
    legend: (c) => `trajectory · ${overlapN(c)}`,
    hint: (c) =>
      `Every candidate on the winner trajectory, read on the same ${unitCount(overlapN(c), c.unit)} — the only pair of bars here that can be differenced.`,
    ink: () => "--color-overlap",
    kind: "bar",
    axis: "y",
    // Only the adopted line is measured on the shared set, so a floored 0 claims an
    // off-line candidate scored nothing rather than that it was never read.
    gap: "sparse",
    valueOf: (v) => v.overlapAccuracy,
    applies: (c) => c.showTrajectory && c.views.some((v) => v.overlapAccuracy != null),
    tip: (v, c) =>
      v.overlapAccuracy == null
        ? "trajectory: not on the winner trajectory"
        : // The COUNT travels with the rate: a percentage over an unnamed denominator is
          // the reading this series exists to replace.
          `trajectory: ${fmtNum(v.overlapAccuracy)}${v.overlapN ? ` on ${unitCount(v.overlapN, c.unit)} shared` : ""}`,
  },
  {
    key: "verify",
    legend: () => "verify",
    hint: () =>
      "A `promptpotter verify` re-run of this candidate over the workspace set — did the verdict hold on more cells?",
    // Shares the evidence ink with `trajectory` and separates by fill, rhyming with the
    // dendrogram's filled-winner / hollow-eliminated dot directly beneath.
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
    // Bridging a gap claims a share for a candidate that has none.
    gap: "sparse",
    valueOf: (v) => {
      const n = v.n_samples;
      return v.cached_samples == null || n == null || n <= 0 ? null : v.cached_samples / n;
    },
    applies: (c) => c.showCache,
    // The served INTEGERS, never the share: the height is geometry, the counts are the
    // measurement, and only the measurement gets written down.
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

// One channel's column, index-aligned with the bars. The RAW value is what tooltips read;
// the floor is a rendering decision and never leaves this array.
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

// The series the confidence band brackets — the elected metric where it is a percent, else
// accuracy, and NULL when neither shows: a band hangs off a bar, so with no bar it must not
// be drawn. A `SeriesKey` rather than a literal, because that join has gone silently missing
// twice.
export function whiskerAnchor(ctx: SeriesCtx): SeriesKey | null {
  // θ's own axis is a logit; a [0,1] mean interval cannot be drawn against it.
  if (ctx.electedMetric !== "ability" && ctx.metrics.has(ctx.electedMetric)) {
    const spec = CANDIDATE_SERIES.find((s) => s.metric === ctx.electedMetric);
    if (spec && spec.axis === "y") return spec.key;
  }
  return ctx.metrics.has("accuracy") ? "accuracy" : null;
}
