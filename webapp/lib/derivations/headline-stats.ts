import type { ArmReading, ServedDashboard, ServedRound } from "@/lib/api/types";
import { DISPLAY_METRICS } from "@/lib/api/types.generated";
import { fmtPct0, fmtTheta } from "@/lib/format";

// DISPLAY only: the selector decides on its own objective (the served `elects_on`), whatever is read.
export type DisplayMetric = ServedDashboard["display_metric"];

// The elected metric first: the bars paint it at full accent, so a node label must print it too.
export function primaryMetric(
  metrics: ReadonlySet<DisplayMetric>,
  elected?: DisplayMetric,
): DisplayMetric {
  if (elected && metrics.has(elected)) return elected;
  return DISPLAY_METRICS.find((m) => metrics.has(m.id))?.id ?? "accuracy";
}

export function metricLevel(
  metric: DisplayMetric,
  reading: ArmReading | null | undefined,
): number | null {
  if (!reading) return null;
  return metric === "ability"
    ? (reading.ability?.theta ?? null)
    : (reading.own?.[metric]?.value ?? null);
}

export function fmtDisplayValue(metric: DisplayMetric, level: number | null): string {
  if (metric === "ability") {
    return typeof level === "number" && Number.isFinite(level) ? `θ ${fmtTheta(level)}` : "—";
  }
  return fmtPct0(level);
}

export interface FitnessTrend {
  // `null` draws a GAP: a point at 0 would claim the prompt scored nothing.
  points: {
    round: number;
    accuracy: number | null;
    composite: number | null;
    theta: number | null;
    bench: number | null;
    n: number;
  }[];
}

// Never `cumulative_accuracy`: it pools rows measured by different configurations.
export function fitnessTrend(rounds: readonly ServedRound[] | undefined): FitnessTrend {
  const served = rounds ?? [];
  const points = served.map((r) => ({
    round: r.round,
    accuracy: r.accuracy,
    composite: r.composite_fitness,
    // θ on another δ ruler than the series' is a different quantity: dropped, not plotted.
    theta: r.ability != null && r.ability_on_series_ruler ? r.ability.theta : null,
    bench: r.bench?.level?.value ?? null,
    n: r.total,
  }));
  return { points };
}
