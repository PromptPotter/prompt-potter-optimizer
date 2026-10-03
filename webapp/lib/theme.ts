// Theme resolution for JS-painted surfaces: a <canvas> cannot resolve var(...), so it reads :root.

import { useSyncExternalStore } from "react";
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

// `animation: false`: the dashboard polls every 2 s, so a tween would re-animate every bar per poll.

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

export function getCss(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function cssRgba(rgbVar: string, alpha: number): string {
  return `rgba(${getCss(rgbVar)},${alpha})`;
}

// Every categorical chart reads `--chart-series-1..8` (tokens.css) through here; never a local copy.
const SERIES_SLOTS = 8;

function seriesToken(index: number): string {
  return `--chart-series-${(index % SERIES_SLOTS) + 1}`;
}

// Resolved, for a `<canvas>` only — re-read it on a theme flip (`useThemeVersion`).
export function seriesColor(index: number): string {
  return getCss(seriesToken(index));
}

// For inline SVG and `style={{}}`, which repaint on a theme flip with no re-render; a swatch
// built from `seriesColor` freezes at the theme of its last render.
export function seriesVar(index: number): string {
  return `var(${seriesToken(index)})`;
}

export function applyChartDefaults(): void {
  ChartJS.defaults.color = getCss("--color-text-secondary");
  ChartJS.defaults.borderColor = getCss("--color-border");
}

const THEME_STORAGE_KEY = "promptpotter.theme";

export type Theme = "light" | "dark";

export function readStoredTheme(): Theme {
  if (typeof window === "undefined") return "light";
  try {
    const v = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (v === "dark" || v === "light") return v;
  } catch {
    /* ignore */
  }
  return "light";
}

export function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  if (t === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, t);
  } catch {
    /* ignore */
  }
  bumpThemeVersion();
}

let themeVersion = 0;
const themeListeners = new Set<() => void>();

function bumpThemeVersion(): void {
  themeVersion += 1;
  applyChartDefaults();
  for (const l of themeListeners) l();
}

function subscribeTheme(cb: () => void): () => void {
  themeListeners.add(cb);
  return () => themeListeners.delete(cb);
}

export function useThemeVersion(): number {
  return useSyncExternalStore(
    subscribeTheme,
    () => themeVersion,
    () => 0,
  );
}
