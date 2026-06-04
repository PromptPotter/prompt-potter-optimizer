"use client";
// Media-query hooks for the mobile-first foundation.
//
// Built on `useSyncExternalStore` so the static export hydrates without a
// flash of the wrong state: the server snapshot is `false` (assume the
// negative case), and the first client tick reads the real `matchMedia`
// value. Same pattern as `useLocalStorage.ts`.
//
// Most responsive behaviour stays in CSS (`@media` rules in app/styles/).
// Reach for these hooks only when the component tree itself needs to
// branch — e.g. skipping an expensive render subtree on portrait phone,
// or swapping a hover affordance for a tap affordance.

import { useCallback, useSyncExternalStore } from "react";

function getMatch(query: string): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia(query).matches;
}

function subscribeMatch(query: string, cb: () => void): () => void {
  const mql = window.matchMedia(query);
  // Both signatures exist in the wild — `addEventListener` is the modern
  // path, `addListener` is the Safari < 14 fallback. We bind whichever the
  // runtime hands back.
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

// True when the primary pointer is coarse (touchscreen — phone, tablet,
// touch-screen laptop). Drives tap-vs-hover affordance swaps where CSS
// alone can't express the choice.
export function useIsTouchPointer(): boolean {
  return useMediaQuery("(pointer: coarse)");
}

// True when the viewport is a phone in portrait. The threshold is
// `--bp-rotate` (768px) — the same value the CSS rotate-prompt block
// uses. Drives the `<RotatePrompt>` JS-side gate; pure-CSS surfaces use
// the matching `@media` rule directly.
export function useIsPortraitPhone(): boolean {
  return useMediaQuery("(orientation: portrait) and (max-width: 767px)");
}
