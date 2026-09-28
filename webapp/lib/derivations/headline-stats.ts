// The one derivation of the headline run KPIs, so no two surfaces show a different headline.

import type { BenchScore, LiveDashboardState, RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0 } from "@/lib/format";

// DISPLAY only: the engine always gates on θ, whatever the operator reads.
export type HeadlineMetric = LiveDashboardState["headline_metric"];

// The one owner of a metric's name, prose and order — `candidates/series.ts` joins to it.
// Order is `primaryMetric`'s tiebreak only; the elected metric wins when it is on.
export const HEADLINE_METRICS: { id: HeadlineMetric; glyph: string; title: string }[] = [
  {
    id: "accuracy",
    glyph: "%",
    title: "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
  },
  {
    id: "ability",
    glyph: "θ",
    title:
      "Difficulty-adjusted ability θ — the metric the winner is actually elected on. A logit (not a %): comparable within a round; cross-round comparison waits on the stable δ bank.",
  },
  {
    id: "composite",
    glyph: "∑",
    title:
      "Composite fitness under the active scoring formula (equals accuracy when no formula is set).",
  },
];

export function headlineMetricLabel(m: HeadlineMetric): string {
  return m === "ability" ? "ability θ" : m === "composite" ? "composite" : "accuracy";
}

// The elected metric first: the bars paint it at full accent, so a node label must print it too.
export function primaryMetric(
  metrics: ReadonlySet<HeadlineMetric>,
  elected?: HeadlineMetric,
): HeadlineMetric {
  if (elected && metrics.has(elected)) return elected;
  return HEADLINE_METRICS.find((m) => metrics.has(m.id))?.id ?? "accuracy";
}

// θ heads a node only where its optimizer stamps one (`LineageNode.stamps_theta`); elsewhere the
// node shows the accuracy it measured, never a blank that reads as a cold ruler.
export function nodeMetric(metric: HeadlineMetric, stampsTheta: boolean): HeadlineMetric {
  return metric === "ability" && !stampsTheta ? "accuracy" : metric;
}

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
  best: number | null;
  origin: number | null;
  // The bench's held-out lift of the selection over the origin, in composite fitness: the
  // headline for every optimizer, potter included. θ never stands in for it.
  benchLift: number | null;
  // Served: its two inputs land on different events, so dividing here would divide two polls.
  benchLiftPerUsd: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // `best` is the max a round MEASURED on the search pool — the optimizer's own reading.
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.accuracy) : null;
  return {
    best,
    origin,
    benchLift: finite(dash?.bench_score?.lift),
    benchLiftPerUsd: finite(dash?.bench_lift_per_incurred_usd),
  };
}

export interface FitnessTrend {
  // `null` draws a GAP: a point at 0 would claim the prompt scored nothing.
  points: {
    round: number;
    accuracy: number | null;
    composite: number | null;
    // Only where the round's own selector elects on θ (`RoundSummary.stamps_theta`).
    theta: number | null;
    // The bench's held-out reading of the pick this round names — the origin or the selection.
    bench: number | null;
    n: number;
  }[];
  best: number[];
}

// Never `cumulative_accuracy`: it pools rows measured by different configurations, so the line can
// sit above everything the cycle measured. Takes `rounds` so callers memo on `dash?.rounds`.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
  servedBest?: number | null,
  bench?: BenchScore | null,
): FitnessTrend {
  const sorted = [...(rounds ?? [])].sort((a, b) => a.round - b.round);
  // θ on a different δ ruler than the first stamped is a different quantity: dropped, not plotted.
  const seriesRuler = sorted.find((r) => r.ability?.ruler_id != null)?.ability?.ruler_id ?? null;
  // The selection first: where the origin IS the selection both readings name round 0.
  const benchOn = (round: number): number | null =>
    bench?.selected?.round === round
      ? bench.selected.composite_fitness
      : bench?.origin?.round === round
        ? bench.origin.composite_fitness
        : null;
  const points = sorted.map((r) => ({
    round: r.round,
    accuracy: r.accuracy,
    // A round with nothing readable serves `accuracy: null`; its composite is no reading either.
    composite: r.accuracy === null ? null : r.composite_fitness,
    theta:
      r.stamps_theta &&
      r.ability != null &&
      r.ability.ruler_id != null &&
      r.ability.ruler_id === seriesRuler
        ? r.ability.theta
        : null,
    bench: benchOn(r.round),
    // The rows the plotted value is a mean over, a held round's included.
    n: r.total,
  }));
  const best: number[] = [];
  let runningBest = 0;
  for (const p of points) {
    if (p.accuracy != null) runningBest = Math.max(runningBest, p.accuracy);
    best.push(runningBest);
  }
  // Anchored to the served `dash.best`: a fork's seed can carry a best its own rounds[] never reach.
  const last = best.length - 1;
  if (last >= 0 && servedBest != null && Number.isFinite(servedBest)) {
    best[last] = Math.max(best[last] ?? 0, servedBest);
  }
  return { points, best };
}
