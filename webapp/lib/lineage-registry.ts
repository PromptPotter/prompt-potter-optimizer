// Who currently wants which tree — the ref-count half of `lib/lineage.tsx`, React-free so
// the invariants below are testable as the pure unit they are.
//
// The version counter feeds `usePoll`'s `revalidateOn`, so a newly opened subscriber
// fetches within a frame instead of waiting out the interval.
//
// The registry also owns each key's ETag. Nothing renders a validator, and it must die
// exactly when its body does — replaying an ETag against a tree we no longer hold would
// 304 into an empty entry. Keeping both here makes that one deletion instead of two that
// have to be remembered together.
//
// Each key latches its FETCH SPEC (`path` + mask opts) from whichever subscriber supplies
// it. The key already encodes the mask (`treeKey`), so every subscriber of one key names
// the same fetch; the provider passes the mask it owns, address-only consumers pass none
// and must not erase it.

import type { CyclePath } from "@/lib/ids";

export type TreeFetchOpts = { lens?: string | null; samples?: number[] | null };

export interface Registry {
  subscribe: (key: string, path: CyclePath, opts?: TreeFetchOpts) => () => void;
  onVersionChange: (listener: () => void) => () => void;
  version: () => number;
  live: () => [string, { path: CyclePath; opts: TreeFetchOpts }][];
  has: (key: string) => boolean;
  etag: (key: string) => string | null;
  setEtag: (key: string, value: string | null) => void;
}

export function createRegistry(onDrop: (key: string) => void): Registry {
  const counts = new Map<string, { count: number; path: CyclePath; opts: TreeFetchOpts }>();
  const etags = new Map<string, string>();
  const listeners = new Set<() => void>();
  let version = 0;
  const bump = (): void => {
    version += 1;
    for (const l of listeners) l();
  };
  return {
    subscribe(key, path, opts) {
      const cur = counts.get(key);
      counts.set(key, { count: (cur?.count ?? 0) + 1, path, opts: opts ?? cur?.opts ?? {} });
      bump();
      return () => {
        const entry = counts.get(key);
        if (!entry) return;
        if (entry.count <= 1) {
          counts.delete(key);
          etags.delete(key);
          onDrop(key);
        } else {
          counts.set(key, { ...entry, count: entry.count - 1 });
        }
        bump();
      };
    },
    onVersionChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    version: () => version,
    live: () => [...counts].map(([k, v]) => [k, { path: v.path, opts: v.opts }]),
    has: (key) => counts.has(key),
    etag: (key) => etags.get(key) ?? null,
    setEtag: (key, value) => {
      if (value) etags.set(key, value);
      else etags.delete(key);
    },
  };
}
