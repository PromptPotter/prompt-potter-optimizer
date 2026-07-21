// Single source of truth for the headline run KPIs — best, origin, and the
// delta between them. Before this, TopStrip and ChatPane each re-ran
// `typeof dash?.best === "number" ? …` and `best - origin` with subtly
// different finite-guards, so the same campaign could show a different
// headline in the chat job-bar than elsewhere. One derivation, they cannot
// disagree.
//
// Origin is round 0 in `dash.rounds[]` (a one-candidate round labelled "C0");
// its accuracy is the round-0 entry's accuracy. The candidate list
// (round-candidates.ts) reads the same round-0 entry through the generic loop.

import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0 } from "@/lib/format";

// Which fitness number headlines a per-candidate text surface. DISPLAY only —
// the engine always GATES on difficulty-adjusted ability θ; this just picks what
// the operator reads (θ is jargon, so it is never the forced default). Seeded
// from the served `dash.headline_metric` (CampaignConfig.headline_metric),
// client-overridable. `composite_fitness` is served on every settled row
// (dashboard candidates AND `/tree` nodes), so all cycles honor the composite
// selection on one basis.
export type HeadlineMetric = "accuracy" | "composite" | "ability";

// The toggle's options, in display order, each with the teaching tooltip that
// keeps θ from reading as an unexplained jargon number (the `AbilityInfo`
// popover carries the long form).
// Display order — accuracy first (always shown; a candidate is rarely bad on it),
// then ability θ (the metric the winner is elected on, the usual second bar), then
// composite. `primaryMetric` reads this order too, so accuracy is the node-label
// metric whenever it is shown.
export const HEADLINE_METRICS: { id: HeadlineMetric; chip: string; title: string }[] = [
  {
    id: "accuracy",
    chip: "Acc",
    title: "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
  },
  {
    id: "ability",
    chip: "θ",
    title:
      "Difficulty-adjusted ability θ — the metric the winner is actually elected on. A logit (not a %): comparable within a round; cross-round comparison waits on the stable δ bank.",
  },
  {
    id: "composite",
    chip: "Comp",
    title:
      "Composite fitness under the active scoring formula (equals accuracy when no formula is set).",
  },
];

export function headlineMetricLabel(m: HeadlineMetric): string {
  return m === "ability" ? "ability θ" : m === "composite" ? "composite" : "accuracy";
}

// The candidates card's metric axis is a SET (the bars can pair several series),
// but a node LABEL is one number. Take the first selected metric in canonical
// `HEADLINE_METRICS` order — deterministic, and it feeds `fmtHeadlineValue`
// unchanged, so the tree renderers need no new formatter.
export function primaryMetric(metrics: ReadonlySet<HeadlineMetric>): HeadlineMetric {
  return HEADLINE_METRICS.find((m) => metrics.has(m.id))?.id ?? "accuracy";
}

// Format one candidate's headline number for the selected metric: a percent for
// accuracy/composite, a 2-dp logit for θ (`θ 0.41`). `null` → "—" either way.
export function fmtHeadlineValue(
  metric: HeadlineMetric,
  pct: number | null,
  theta: number | null,
): string {
  if (metric === "ability") {
    return typeof theta === "number" && Number.isFinite(theta) ? `θ ${theta.toFixed(2)}` : "—";
  }
  return fmtPct0(pct);
}

export interface HeadlineStats {
  // Served rolling-max cumulative_accuracy, finite or null.
  best: number | null;
  // Origin's round-0 cumulative_accuracy (same basis as `best`), finite or null.
  origin: number | null;
  // best − origin when both are present; null otherwise.
  delta: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // `best` is the server-side rolling max of `rounds[].cumulative_accuracy` — the
  // incumbent's full-population score (LiveDashboardView._absorb_round_complete is
  // the sole writer; it is NOT composite-based). `delta` is SERVED
  // (`headline_delta`, the same basis) — never recomputed here, so this chip and
  // the L4 inner progress line read one number (R-36).
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.cumulative_accuracy) : null;
  const delta = finite(dash?.headline_delta);
  return { best, origin, delta };
}

export interface FitnessTrend {
  // Per-round cumulative_accuracy, ascending.
  points: { round: number; composite: number }[];
  // Running-best composite, index-aligned with `points`.
  best: number[];
}

// One definition of the campaign trend behind the TopStrip sparkline and the
// TrendChart best-line, so they can't drift on the source rule or the
// running-best fold. Takes `rounds` so callers memo on `dash?.rounds`.
//
// Plots `cumulative_accuracy` — the incumbent lineage rescored over EVERY sample
// probed so far — NOT the per-round `accuracy`/`composite_fitness`. Those are
// subset-relative: under `per_round_resubset` each round scores a fresh 6–16
// hard-first sample draw, so the same prompt swings ±0.2–0.3 and reads as a
// false "great start → decay". The cumulative frontier is the honest,
// cross-round-comparable progress line (it matches the Best/current tiles, which
// already settle to cumulative). The per-round subset number stays visible on the
// candidate rows, badged with its sample count.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
  servedBest?: number | null,
): FitnessTrend {
  const points = (rounds ?? [])
    .map((r) => ({ round: r.round, composite: r.cumulative_accuracy }))
    .sort((a, b) => a.round - b.round);
  const best: number[] = [];
  let runningBest = 0;
  for (const p of points) {
    runningBest = Math.max(runningBest, p.composite);
    best.push(runningBest);
  }
  // Anchor the terminal point to the SERVED `dash.best` (the engine's own fold,
  // `_absorb_round_complete`) so the chart's running-best line can never end
  // below the Best tile — e.g. a fork whose seed carried a best its own
  // rounds[] doesn't reach.
  const last = best.length - 1;
  if (last >= 0 && servedBest != null && Number.isFinite(servedBest)) {
    best[last] = Math.max(best[last] ?? 0, servedBest);
  }
  return { points, best };
}
