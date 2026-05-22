// Shared chart-option bases. Every dashboard chart is `responsive` and fills
// its container; each caller spreads its own `plugins` / `scales` over the
// base (a shallow spread — axis/legend config stays at the call site, where
// it visibly diverges chart to chart).

import type { ChartOptions } from "chart.js";

export function lineChartDefaults(
  over?: Partial<ChartOptions<"line">>,
): ChartOptions<"line"> {
  return { responsive: true, maintainAspectRatio: false, ...over };
}

export function barChartDefaults(
  over?: Partial<ChartOptions<"bar">>,
): ChartOptions<"bar"> {
  return { responsive: true, maintainAspectRatio: false, ...over };
}
