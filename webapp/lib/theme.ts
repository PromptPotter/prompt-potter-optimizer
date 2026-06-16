// Theme-aware color resolution for JS-painted things (Chart.js, SVG markers).
// Canvas/SVG can't resolve var(...); these read off :root at call time.

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

export function getCss(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function cssRgba(rgbVar: string, alpha: number): string {
  return `rgba(${getCss(rgbVar)},${alpha})`;
}

// Updates Chart.defaults to the current theme's CSS-var colours. Called
// once on mount and re-called on every theme flip via bumpThemeVersion.
export function applyChartDefaults(): void {
  ChartJS.defaults.color = getCss("--color-text-secondary");
  ChartJS.defaults.borderColor = getCss("--color-border-tertiary");
}

export const THEME_STORAGE_KEY = "promptpotter.theme";

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

// Subscribable theme-change counter. Bumped after applyTheme flips the
// data-theme attribute; canvas/SVG consumers that read CSS vars at paint
// time subscribe via useThemeVersion() and use the counter as a memo dep
// — no more synthetic themeKey prop carried through the tree.
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
