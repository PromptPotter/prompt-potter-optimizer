// The one derivation of the headline run KPIs, so no two surfaces show a different headline.

import type { LiveDashboardState, RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0, fmtTheta } from "@/lib/format";

// DISPLAY only: the selector decides on its own objective (θ where it stamps one), whatever is read.
export type DisplayMetric = LiveDashboardState["display_metric"];

// The one owner of a metric's name, prose and order — `candidates/series.ts` joins to it.
// Order is `primaryMetric`'s tiebreak only; the elected metric wins when it is on.
export const DISPLAY_METRICS: { id: DisplayMetric; glyph: string; title: string }[] = [
  {
    id: "accuracy",
    glyph: "%",
    title: "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
  },
  {
    id: "ability",
    glyph: "θ",
    title:
      "Difficulty-adjusted ability θ — what a θ-stamping selector (potter's) elects on. A logit (not a %): comparable within a round; cross-round comparison waits on the stable δ bank.",
  },
  {
    id: "composite",
    glyph: "∑",
    title:
      "Composite fitness under the active scoring formula (equals accuracy when no formula is set).",
  },
];

export function displayMetricLabel(m: DisplayMetric): string {
  return m === "ability" ? "ability θ" : m === "composite" ? "composite" : "accuracy";
}

// The elected metric first: the bars paint it at full accent, so a node label must print it too.
export function primaryMetric(
  metrics: ReadonlySet<DisplayMetric>,
  elected?: DisplayMetric,
): DisplayMetric {
  if (elected && metrics.has(elected)) return elected;
  return DISPLAY_METRICS.find((m) => metrics.has(m.id))?.id ?? "accuracy";
}

// θ heads a node only where its optimizer stamps one (`LineageNode.stamps_theta`); elsewhere the
// node shows the accuracy it measured, never a blank that reads as a cold ruler.
export function nodeMetric(metric: DisplayMetric, stampsTheta: boolean): DisplayMetric {
  return metric === "ability" && !stampsTheta ? "accuracy" : metric;
}

export function fmtDisplayValue(
  metric: DisplayMetric,
  pct: number | null,
  theta: number | null,
): string {
  if (metric === "ability") {
    return typeof theta === "number" && Number.isFinite(theta) ? `θ ${fmtTheta(theta)}` : "—";
  }
  return fmtPct0(pct);
}

export interface HeadlineStats {
  best: number | null;
  origin: number | null;
  // The bench's held-out lift of the selection over the origin, in the served headline column:
  // the headline for every optimizer, potter included. θ never stands in for it.
  benchLift: number | null;
  // Served: its two inputs land on different events, so dividing here would divide two polls.
  benchLiftPerUsd: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // `best` is the best round on the shared origin-panel cells — the cycle index's number.
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.accuracy) : null;
  const bench = dash?.bench_score;
  return {
    best,
    origin,
    // Guarded: `dashboard.json` is served verbatim, and a file an older build wrote names no column.
    benchLift: bench?.headline ? finite(bench.lift[bench.headline]?.value) : null,
    benchLiftPerUsd: finite(dash?.bench_lift_per_incurred_usd),
  };
}

export interface FitnessTrend {
  // `null` draws a GAP: a point at 0 would claim the prompt scored nothing.
  points: {
    round: number;
    accuracy: number | null;
    composite: number | null;
    // Served only where the round's own selector elects on θ (`RoundSummary.ability`).
    theta: number | null;
    // The bench's held-out grade of the selection this round declared (`RoundSummary.bench`).
    bench: number | null;
    n: number;
  }[];
  // The served BEST line (`RoundSummary.best_so_far`), `null` before any round measured.
  best: (number | null)[];
}

// Never `cumulative_accuracy`: it pools rows measured by different configurations, so the line can
// sit above everything the cycle measured. Takes `rounds` so callers memo on `dash?.rounds`.
export function fitnessTrend(rounds: readonly RoundSummary[] | undefined): FitnessTrend {
  const served = rounds ?? [];
  const points = served.map((r) => ({
    round: r.round,
    accuracy: r.accuracy,
    // A round with nothing readable serves `accuracy: null`; its composite is no reading either.
    composite: r.accuracy === null ? null : r.composite_fitness,
    // θ on another δ ruler than the series' is a different quantity: dropped, not plotted. Which
    // rounds share that ruler is served (`ability_on_series_ruler`).
    theta: r.ability != null && r.ability_on_series_ruler ? r.ability.theta : null,
    bench: r.bench?.headline ? (r.bench[r.bench.headline]?.value ?? null) : null,
    // The rows the plotted value is a mean over, a held round's included.
    n: r.total,
  }));
  return { points, best: served.map((r) => r.best_so_far) };
}
