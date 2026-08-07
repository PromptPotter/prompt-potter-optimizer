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
  // Served rolling-max of what each round MEASURED, finite or null.
  best: number | null;
  // Origin's round-0 measured accuracy (same basis as `best`), finite or null.
  origin: number | null;
  // Served lift over origin, in LOGITS on the cycle's fixed δ ruler — NOT a fraction, so
  // never format it with a percent. It is deliberately not `best − origin`: under
  // `per_round_resubset` each round draws a fresh subset, so that difference is the luckiest
  // draw minus the fullest one and read `+19%` off a cycle whose ability never moved. Ability
  // is the only cross-round-comparable series (see `RoundSummary.cumulative_theta`).
  abilityDelta: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // `best` is the server-side rolling max of `rounds[].accuracy` — what each round
  // actually MEASURED (LiveDashboardView._absorb_round_complete is the sole writer;
  // it is NOT composite-based). `abilityDelta` is SERVED (`ability_delta`) — never
  // recomputed here, so this chip and the L4 inner progress line read one number (R-36).
  // The two are on DIFFERENT bases now, deliberately: `best` answers "what did a round
  // measure", `abilityDelta` answers "how far above origin is the incumbent".
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.accuracy) : null;
  const abilityDelta = finite(dash?.ability_delta);
  return { best, origin, abilityDelta };
}

export interface FitnessTrend {
  // Per-round measured accuracy, ascending.
  points: { round: number; composite: number }[];
  // Running-best composite, index-aligned with `points`.
  best: number[];
}

// One definition of the campaign trend behind the TopStrip sparkline and the
// TrendChart best-line, so they can't drift on the source rule or the
// running-best fold. Takes `rounds` so callers memo on `dash?.rounds`.
//
// Plots each round's MEASURED `accuracy` — the elected winner on the samples that
// round drew, or a held round's retained incumbent re-scored on them. It matches the
// Best/current tiles, which settle to the same basis.
//
// It used to plot `cumulative_accuracy`, "the incumbent lineage rescored over EVERY
// sample probed so far". No rescore happened: that series pooled rows measured by
// DIFFERENT configurations, so the line could sit above everything the cycle had
// measured (`57%→78%` on a run whose best candidate reached 0.679).
//
// The concern it was answering is real — under `per_round_resubset` each round scores
// a fresh hard-first draw, so the same prompt swings and can read as a false "great
// start → decay". The honest fix is the round's own sample count beside the number,
// and `RoundSummary.cumulative_theta` (ability on the cycle's fixed δ ruler, which IS
// subset-invariant) once this chart grows a logit axis to plot it on.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
  servedBest?: number | null,
): FitnessTrend {
  const points = (rounds ?? [])
    .map((r) => ({ round: r.round, composite: r.accuracy }))
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
