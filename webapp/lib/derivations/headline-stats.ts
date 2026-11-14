import { type ArmReading, DISPLAY_METRIC_INFO, type DisplayMetric } from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";

// DISPLAY only: the selector decides on its own objective (the served `elects_on`), whatever is read.

// The elected metric first: the bars paint it at full accent, so a node label must print it too.
export function primaryMetric(
  metrics: ReadonlySet<DisplayMetric>,
  elected?: DisplayMetric,
): DisplayMetric {
  if (elected && metrics.has(elected)) return elected;
  return wordKeys(DISPLAY_METRIC_INFO).find((m) => metrics.has(m)) ?? "fitness";
}

export function metricLevel(
  metric: DisplayMetric,
  reading: ArmReading | null | undefined,
): number | null {
  if (!reading) return null;
  return metric === "ability"
    ? (reading.ability?.theta?.value ?? null)
    : (reading.own?.[metric]?.value ?? null);
}
