"use client";
// Only for when the component TREE must branch; responsive styling stays in `@media`.

import { useCallback, useSyncExternalStore } from "react";

function getMatch(query: string): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia(query).matches;
}

function subscribeMatch(query: string, cb: () => void): () => void {
  const mql = window.matchMedia(query);
  // `addListener` is Safari < 14's only form.
  if ("addEventListener" in mql) {
    mql.addEventListener("change", cb);
    return () => mql.removeEventListener("change", cb);
  }
  (mql as MediaQueryList).addListener(cb);
  return () => (mql as MediaQueryList).removeListener(cb);
}

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (cb: () => void) => subscribeMatch(query, cb),
    [query],
  );
  const getSnapshot = useCallback(() => getMatch(query), [query]);
  const getServerSnapshot = useCallback(() => false, []);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

// Mirrors `--bp-rotate`; a media query cannot read a custom property.
export function useIsPortraitPhone(): boolean {
  return useMediaQuery("(orientation: portrait) and (max-width: 767px)");
}

// Mirrors `--bp-sm`, so a JS branch flips with the `@media` rules around it.
export function useIsPhone(): boolean {
  return useMediaQuery("(max-width: 640px)");
}
