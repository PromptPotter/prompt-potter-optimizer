// Theme-aware color resolution for JS-painted things (Chart.js, SVG markers).
// Lifted from webapp/index.html:890 + :896 (vanilla preservation list).
// Canvas/SVG can't resolve var(...); these read off :root at call time.

import { useSyncExternalStore } from "react";
import { Chart as ChartJS } from "chart.js";

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
