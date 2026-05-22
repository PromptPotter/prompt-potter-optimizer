"use client";
// Chart.js v4 setup: explicit component registration + shared option bases.
// One module imported once before any chart renders.

import {
  Chart as ChartJS,
  BarElement,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Filler,
  BarController,
  LineController,
  type ChartOptions,
} from "chart.js";

// Chart.js v4 requires explicit registration of the components used.
let registered = false;

export function ensureChartRegistered(): void {
  if (registered) return;
  ChartJS.register(
    BarElement,
    LineElement,
    PointElement,
    CategoryScale,
    LinearScale,
    Tooltip,
    Filler,
    BarController,
    LineController,
  );
  registered = true;
}

// Shared chart-option bases. Every dashboard chart is `responsive` and fills
// its container; each caller spreads its own `plugins` / `scales` over the
// base (a shallow spread — axis/legend config stays at the call site, where
// it visibly diverges chart to chart).

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
