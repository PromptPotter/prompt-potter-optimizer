// The React-free ref-count half of `lib/lineage.tsx`. An unsubscribed key's body and ETag are
// RETAINED and dropped as a pair: a stale ETag would 304 into an empty entry.

import type { CyclePath } from "@/lib/ids";

export type TreeFetchOpts = { lens?: string | null; samples?: number[] | null };

export const RETAINED_TREE_KEYS = 16;

export interface Registry {
  subscribe: (key: string, path: CyclePath, opts?: TreeFetchOpts) => () => void;
  onVersionChange: (listener: () => void) => () => void;
  version: () => number;
  /** A key `rests` names leaves the poll once it holds a validated body. */
  liveKeys: (rests?: (path: CyclePath) => boolean) => string[];
  /** Null = no longer subscribed. */
  spec: (key: string) => { path: CyclePath; opts: TreeFetchOpts } | null;
  etag: (key: string) => string | null;
  setEtag: (key: string, value: string | null) => void;
  /** Sticky 404: a re-mint gets a new key, so a still-mounted subscriber stops re-asking. */
  markGone: (key: string) => void;
  isGone: (key: string) => boolean;
}

export function createRegistry(onDrop: (key: string) => void): Registry {
  const counts = new Map<string, { count: number; path: CyclePath; opts: TreeFetchOpts }>();
  const etags = new Map<string, string>();
  const gone = new Set<string>();
  // Unsubscribed keys still holding a body, oldest first.
  const retained = new Set<string>();
  const listeners = new Set<() => void>();
  let version = 0;
  const bump = (): void => {
    version += 1;
    for (const l of listeners) l();
  };
  const drop = (key: string): void => {
    retained.delete(key);
    etags.delete(key);
    onDrop(key);
  };
  return {
    subscribe(key, path, opts) {
      const cur = counts.get(key);
      retained.delete(key);
      // An address-only subscriber passes no opts and must not erase the latched mask.
      counts.set(key, { count: (cur?.count ?? 0) + 1, path, opts: opts ?? cur?.opts ?? {} });
      bump();
      return () => {
        const entry = counts.get(key);
        if (!entry) return;
        if (entry.count <= 1) {
          counts.delete(key);
          // Leaking the gone mark would make a re-subscribe silently unfetchable.
          if (gone.delete(key)) {
            drop(key);
          } else {
            retained.add(key);
            for (const oldest of retained) {
              if (retained.size <= RETAINED_TREE_KEYS) break;
              drop(oldest);
            }
          }
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
    liveKeys: (rests) =>
      [...counts]
        .filter(([k, v]) => !gone.has(k) && !(etags.has(k) && rests?.(v.path)))
        .map(([k]) => k),
    spec: (key) => {
      const v = counts.get(key);
      return v ? { path: v.path, opts: v.opts } : null;
    },
    etag: (key) => etags.get(key) ?? null,
    setEtag: (key, value) => {
      if (value) etags.set(key, value);
      else etags.delete(key);
    },
    markGone: (key) => {
      if (!counts.has(key) || gone.has(key)) return;
      gone.add(key);
      etags.delete(key);
      bump();
    },
    isGone: (key) => gone.has(key),
  };
}
