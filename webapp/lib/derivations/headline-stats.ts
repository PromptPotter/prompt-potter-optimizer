// The one derivation of the headline run KPIs, so no two surfaces show a different headline.

import type { LiveDashboardState, RoundSummary } from "@/lib/api/types";
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
  // LOGITS, never a percent. Never `best − origin`: under `per_round_resubset` that is the
  // luckiest draw minus the fullest one.
  abilityDelta: number | null;
  // Served: its two inputs land on different events, so dividing here would divide two polls.
  abilityDeltaPerUsd: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // Two bases on purpose: `best` is the max a round MEASURED (accuracy, not composite);
  // `abilityDelta` is how far above origin the parent is.
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.accuracy) : null;
  const abilityDelta = finite(dash?.ability_delta);
  return { best, origin, abilityDelta, abilityDeltaPerUsd: finite(dash?.ability_delta_per_usd) };
}

export interface FitnessTrend {
  // `null` composite draws a GAP: a point at 0 would claim the prompt scored nothing.
  points: { round: number; composite: number | null; theta: number | null; n: number | null }[];
  best: number[];
}

// Never `cumulative_accuracy`: it pools rows measured by different configurations, so the line can
// sit above everything the cycle measured. Takes `rounds` so callers memo on `dash?.rounds`.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
  servedBest?: number | null,
): FitnessTrend {
  const sorted = [...(rounds ?? [])].sort((a, b) => a.round - b.round);
  // θ on a different δ ruler than the first stamped is a different quantity: dropped, not plotted.
  const seriesRuler = sorted.find((r) => r.ability?.ruler_id != null)?.ability?.ruler_id ?? null;
  const points = sorted.map((r) => ({
    round: r.round,
    composite: r.accuracy,
    theta:
      r.ability != null && r.ability.ruler_id != null && r.ability.ruler_id === seriesRuler
        ? r.ability.theta
        : null,
    // Every arm of one round measured the same draw.
    n: r.candidates.find((c) => c.is_winner)?.scored_samples ?? null,
  }));
  const best: number[] = [];
  let runningBest = 0;
  for (const p of points) {
    if (p.composite != null) runningBest = Math.max(runningBest, p.composite);
    best.push(runningBest);
  }
  // Anchored to the served `dash.best`: a fork's seed can carry a best its own rounds[] never reach.
  const last = best.length - 1;
  if (last >= 0 && servedBest != null && Number.isFinite(servedBest)) {
    best[last] = Math.max(best[last] ?? 0, servedBest);
  }
  return { points, best };
}
