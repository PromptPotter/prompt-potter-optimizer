"use client";
// The one localStorage-backed state hook, synced within the tab and across tabs.

import { useCallback, useRef, useSyncExternalStore } from "react";

const listeners = new Map<string, Set<() => void>>();
// `useSyncExternalStore` needs a stable reference: re-parsing per read would loop.
const snapCache = new Map<string, { raw: string | null; parsed: unknown }>();

function emit(key: string): void {
  listeners.get(key)?.forEach((l) => l());
}

interface Options<T> {
  serialize?: (value: T) => string;
  deserialize?: (raw: string) => T;
}

export function useLocalStorage<T>(
  key: string,
  initial: T,
  opts?: Options<T>,
): [T, (next: T | ((prev: T) => T)) => void] {
  // First render's values, so the callbacks depend on `key` alone.
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
