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
//
// `animation: false` is the project-wide default. The dashboard polls
// dashboard.json every 2 s; a 200 ms tween that interpolates every bar's
// height on every poll is a constant frame-cost for a metric that already
// changes legibly without animation. Operators who want a smoother update
// can override at the call site.

export function lineChartDefaults(
  over?: Partial<ChartOptions<"line">>,
): ChartOptions<"line"> {
  return { responsive: true, maintainAspectRatio: false, animation: false, ...over };
}

export function barChartDefaults(
  over?: Partial<ChartOptions<"bar">>,
): ChartOptions<"bar"> {
  return { responsive: true, maintainAspectRatio: false, animation: false, ...over };
}
