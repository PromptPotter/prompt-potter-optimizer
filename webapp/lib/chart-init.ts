"use client";
// Chart.js v4 requires explicit registration of the components used.
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
} from "chart.js";

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
