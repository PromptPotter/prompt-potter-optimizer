"use client";

import { useSyncExternalStore } from "react";

export interface ModuleStore<T> {
  get: () => T;
  set: (patch: Partial<T>) => void;
  useStore: () => T;
}

export function createModuleStore<T extends object>(initial: T): ModuleStore<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  const subscribe = (l: () => void): (() => void) => {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  };
  const get = (): T => state;
  return {
    get,
    set(patch) {
      state = { ...state, ...patch };
      // Deferred: a per-cycle seed writes during render, and React refuses an update mid-render.
      queueMicrotask(() => {
        for (const l of listeners) l();
      });
    },
    useStore: () => useSyncExternalStore(subscribe, get, get),
  };
}
