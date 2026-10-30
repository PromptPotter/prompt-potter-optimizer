"use client";

import type { MeasuredUnit, VerificationMark } from "@/lib/api/types";
import { READING_STATE_INFO, VERIFICATION_KIND_INFO } from "@/lib/api/types.generated";
import { barLevel, barStarted, type DisplayMetric } from "@/lib/derivations";
import { fmtInterval, fmtUnit, PLACEHOLDER, unitCount } from "@/lib/format";
import type { CandidateBar } from "@/lib/types";

type SeriesKey =
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

interface SeriesSpec {
  key: SeriesKey;
  metric?: DisplayMetric;
  legend?: (ctx: SeriesCtx) => string;
  hint?: (ctx: SeriesCtx) => string;
  ink: (ctx: SeriesCtx) => string;
  // A mark is a diamond at its candidate's slot: a check on the bar, never a second bar.
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

function markTip(m: VerificationMark | null, unit: MeasuredUnit): string {
  if (m == null) return PLACEHOLDER;
  const { name, rows } = VERIFICATION_KIND_INFO[m.kind];
  if (m.failed_as != null) return `${name}, failed: ${READING_STATE_INFO[m.failed_as].sentence}`;
  const level = m.level;
  const band =
    level?.ci_lo != null && level.ci_hi != null ? ` ${fmtInterval("rate", level.ci_lo, level.ci_hi)}` : "";
  if (m.state === "running") {
    const sofar = level == null ? "" : `, ${fmtUnit("rate", level.value)} so far`;
    return `${name}, ${m.scored} of ${unitCount(m.rows, unit)}${rows}${sofar}`;
  }
  const read = level == null ? ", no level" : `, ${fmtUnit("rate", level.value)}${band}`;
  return `${name}, ${unitCount(m.scored, unit)}${rows}${read}`;
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
    tip: (v) => `accuracy: ${fmtUnit("level", barLevel("accuracy", v))}`,
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
    tip: (v) => `ability: ${fmtUnit("theta", barLevel("ability", v))}`,
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
    tip: (v) => `composite: ${fmtUnit("composed", barLevel("composite", v))}`,
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
    tip: (v) => `masked: ${fmtUnit("composed", v.reading ? v.node.facts.lens_value : null)}`,
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
        : `overlap: ${fmtUnit("level", v.overlap.rate)}${v.overlap.n ? ` on ${unitCount(v.overlap.n, c.unit)} shared` : ""}`,
  },
  {
    key: "verify",
    legend: () => "verify",
    hint: () =>
      "An open diamond: this candidate on search cells its rounds never bought, read on those fresh cells alone — the check on the level its bar claims. Picked hardest-first, they sit below that level by construction.",
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
      "A filled diamond: this candidate on the held-out bench set — questions no round of the search ever read. Graded for the origin as the run starts and for the selection as it ends, so most bars carry none.",
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
        ? `cached: ${PLACEHOLDER}`
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
