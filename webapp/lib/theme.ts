// Theme-aware color resolution for JS-painted things (Chart.js, SVG markers).
// Lifted from webapp/index.html:890 + :896 (vanilla preservation list).
// Canvas/SVG can't resolve var(...); these read off :root at call time.

import type { Chart } from "chart.js";

export function getCss(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function cssRgba(rgbVar: string, alpha: number): string {
  return `rgba(${getCss(rgbVar)},${alpha})`;
}

// Re-callable on theme switch — called by ThemeToggle after flipping
// data-theme. Updates Chart.defaults; existing chart instances should
// re-render via .update().
export function applyChartDefaults(ChartCtor?: typeof Chart): void {
  const C = ChartCtor ?? (typeof window !== "undefined" ? (window as unknown as { Chart?: typeof Chart }).Chart : undefined);
  if (!C || !("defaults" in C)) return;
  C.defaults.color = getCss("--color-text-secondary");
  C.defaults.borderColor = getCss("--color-border-tertiary");
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
}
