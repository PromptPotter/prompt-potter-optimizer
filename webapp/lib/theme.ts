import { useSyncExternalStore } from "react";
import { STORAGE_KEYS } from "@/lib/storage-keys";

export function getCss(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// `--chart-series-1..8` in `foundation/tokens.css`.
const SERIES_SLOTS = 8;

function seriesToken(index: number): string {
  return `--chart-series-${(index % SERIES_SLOTS) + 1}`;
}

export function seriesColor(index: number): string {
  return getCss(seriesToken(index));
}

export function seriesVar(index: number): string {
  return `var(${seriesToken(index)})`;
}

type Theme = "light" | "dark";

export function readStoredTheme(): Theme {
  try {
    const v = window.localStorage.getItem(STORAGE_KEYS.theme);
    if (v === "dark" || v === "light") return v;
  } catch {
    /* ignore */
  }
  return "light";
}

export function applyTheme(t: Theme): void {
  if (t === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  try {
    window.localStorage.setItem(STORAGE_KEYS.theme, t);
  } catch {
    /* ignore */
  }
  bumpThemeVersion();
}

let themeVersion = 0;
const themeListeners = new Set<() => void>();

function bumpThemeVersion(): void {
  themeVersion += 1;
  for (const l of themeListeners) l();
}

export function subscribeTheme(cb: () => void): () => void {
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
