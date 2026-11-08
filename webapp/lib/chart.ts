import {
  Chart as ChartJS,
  BarElement,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Filler,
  BarController,
  LineController,
  type ChartOptions,
} from "chart.js";
import { getCss, subscribeTheme } from "@/lib/theme";

// Chart.js v4 requires explicit registration of the components used. Never its tooltip plugin: a
// canvas paints no detail over its own marks — what a tap reads opens in the DOM below the chart.
let registered = false;

export function ensureChartRegistered(): void {
  if (registered) return;
  ChartJS.register(
    BarElement,
    LineElement,
    PointElement,
    CategoryScale,
    LinearScale,
    Filler,
    BarController,
    LineController,
  );
  // Before any chart mounts: a theme change re-inks the axes ahead of a chart's re-render.
  subscribeTheme(applyChartDefaults);
  registered = true;
}

// `animation: false`: a tween would re-animate every bar on each poll.
export function lineChartDefaults(
  over?: Partial<ChartOptions<"line">>,
): ChartOptions<"line"> {
  return { responsive: true, maintainAspectRatio: false, animation: false, ...over };
}

export function lineLook(ink: string, dashed = false) {
  return {
    borderColor: ink,
    backgroundColor: ink,
    borderWidth: 1.5,
    tension: 0.3,
    pointRadius: 2,
    ...(dashed ? { borderDash: [4, 3] } : {}),
  };
}

export function lineMotion(reducedMotion: boolean) {
  return reducedMotion ? (false as const) : { duration: 1000, easing: "easeOutQuart" as const };
}

export function barChartDefaults(
  over?: Partial<ChartOptions<"bar">>,
): ChartOptions<"bar"> {
  return { responsive: true, maintainAspectRatio: false, animation: false, ...over };
}

export function applyChartDefaults(): void {
  ChartJS.defaults.color = getCss("--color-text-secondary");
  ChartJS.defaults.borderColor = getCss("--color-border");
}
