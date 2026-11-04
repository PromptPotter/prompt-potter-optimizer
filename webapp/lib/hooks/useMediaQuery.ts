// Only for when the component TREE must branch; responsive styling stays in `@media`.

import { useCallback, useSyncExternalStore } from "react";

function getMatch(query: string): boolean {
  return window.matchMedia(query).matches;
}

function subscribeMatch(query: string, cb: () => void): () => void {
  const mql = window.matchMedia(query);
  mql.addEventListener("change", cb);
  return () => mql.removeEventListener("change", cb);
}

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (cb: () => void) => subscribeMatch(query, cb),
    [query],
  );
  const getSnapshot = useCallback(() => getMatch(query), [query]);
  return useSyncExternalStore(subscribe, getSnapshot);
}
