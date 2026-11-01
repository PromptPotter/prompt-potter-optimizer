import type { ArmReading, ServedDashboard } from "@/lib/api/types";
import { DISPLAY_METRICS } from "@/lib/api/types.generated";

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
