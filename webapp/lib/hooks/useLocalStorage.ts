"use client";
// One localStorage-backed state hook, built on `useSyncExternalStore` — the
// same external-store pattern as candidates-store.ts and Lane.tsx. The server
// snapshot is the default, so the static export's first render matches the
// server HTML. Components bound to the same key stay in sync within the tab
// (a module-level listener registry) and across tabs (the `storage` event).

import { useCallback, useRef, useSyncExternalStore } from "react";

const listeners = new Map<string, Set<() => void>>();
// Parsed-snapshot cache, keyed by storage key. `useSyncExternalStore` demands
// a stable reference between reads — re-parsing JSON each call would hand back
// a fresh object every render and loop. Invalidated on every write.
const snapCache = new Map<string, { raw: string | null; parsed: unknown }>();

function emit(key: string): void {
  listeners.get(key)?.forEach((l) => l());
}

interface Options<T> {
  // Default codec is JSON. Override for shapes JSON can't round-trip (a Set),
  // or to merge a partial stored blob against a default.
  serialize?: (value: T) => string;
  deserialize?: (raw: string) => T;
}

export function useLocalStorage<T>(
  key: string,
  initial: T,
  opts?: Options<T>,
): [T, (next: T | ((prev: T) => T)) => void] {
  // Default + codecs are conceptually constant per usage — capture the first
  // render's values so the callbacks below depend on `key` alone.
  const cfg = useRef({
    initial,
    serialize: opts?.serialize ?? (JSON.stringify as (v: T) => string),
    deserialize: opts?.deserialize ?? (JSON.parse as (raw: string) => T),
  });

  const subscribe = useCallback((cb: () => void) => {
    let bucket = listeners.get(key);
    if (!bucket) {
      bucket = new Set();
      listeners.set(key, bucket);
    }
    bucket.add(cb);
    const onStorage = (e: StorageEvent) => {
      if (e.key === key) {
        snapCache.delete(key);
        cb();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      bucket.delete(cb);
      window.removeEventListener("storage", onStorage);
    };
  }, [key]);

  const getSnapshot = useCallback((): T => {
    let raw: string | null;
    try {
      raw = window.localStorage.getItem(key);
    } catch {
      return cfg.current.initial;
    }
    const cached = snapCache.get(key);
    if (cached && cached.raw === raw) return cached.parsed as T;
    let parsed: T;
    try {
      parsed = raw == null ? cfg.current.initial : cfg.current.deserialize(raw);
    } catch {
      parsed = cfg.current.initial;
    }
    snapCache.set(key, { raw, parsed });
    return parsed;
  }, [key]);

  // Server / pre-hydration snapshot — the default, so first paint matches.
  const getServerSnapshot = useCallback((): T => cfg.current.initial, []);

  const value = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const store = useCallback(
    (next: T | ((prev: T) => T)) => {
      const prev = getSnapshot();
      const resolved =
        typeof next === "function" ? (next as (p: T) => T)(prev) : next;
      try {
        window.localStorage.setItem(key, cfg.current.serialize(resolved));
      } catch {
        /* private mode / quota — nothing persists */
        return;
      }
      snapCache.delete(key);
      emit(key);
    },
    [key, getSnapshot],
  );

  return [value, store];
}
