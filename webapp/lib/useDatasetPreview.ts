"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples table. Module-level LRU
// keyed on (campaignId, cycleId, scope); on miss, the hook fetches and
// shows the prior slice in the meantime (isStale: true) so the table never
// blanks. Each scope is purely itself — no fallback between campaign and
// dataset slices; the toggle is display-only.

import { useEffect, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "./api";

interface ScopeSlice {
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  archivePerSample: Map<number, MeasurementDot[]>;
}

export interface DatasetPreviewState extends ScopeSlice {
  datasetName: string | null;
  splitTest: number | null;
  isStale: boolean;
}

interface CacheEntry {
  datasetName: string;
  splitTest: number | null;
  slice: ScopeSlice;
}

const EMPTY: DatasetPreviewState = {
  datasetName: null,
  splitTest: null,
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  archivePerSample: new Map(),
  isStale: false,
};

// Module-level LRU. Map preserves insertion order; we read-touch by
// delete+set on every hit and evict the oldest on overflow.
const CACHE_LIMIT = 12;
const cache = new Map<string, CacheEntry>();

function cacheGet(k: string): CacheEntry | undefined {
  const v = cache.get(k);
  if (v !== undefined) {
    cache.delete(k);
    cache.set(k, v);
  }
  return v;
}

function cacheSet(k: string, v: CacheEntry): void {
  if (cache.has(k)) cache.delete(k);
  cache.set(k, v);
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

function dotMap(
  items: { sample_id: number; measurements: MeasurementDot[] }[] | undefined,
): Map<number, MeasurementDot[]> {
  const m = new Map<number, MeasurementDot[]>();
  if (items) for (const s of items) m.set(s.sample_id, s.measurements);
  return m;
}

// "Measured" = at least one dot in the History column — same signal the
// "Hide unmeasured" tick uses, so the footer never lies about what the tick
// would hide.
function sliceFrom(
  items: DatasetItem[],
  series: { sample_id: number; measurements: MeasurementDot[] }[] | undefined,
): ScopeSlice {
  const archivePerSample = dotMap(series);
  let measured = 0;
  for (const it of items) {
    if ((archivePerSample.get(it.sample_id)?.length ?? 0) > 0) measured += 1;
  }
  return {
    items,
    measuredCount: measured,
    unmeasuredCount: items.length - measured,
    archivePerSample,
  };
}

export function useDatasetPreview(
  campaignId: string | null,
  cycleId: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  const wantKey =
    campaignId && cycleId ? `${campaignId}::${cycleId}::${scope}` : null;

  // Last successful fetch — kept across key changes so the table shows the
  // prior slice (marked stale) while a new fetch is in flight. The effect's
  // cancel-guard ensures only a fetch for the current wantKey writes here.
  const [last, setLast] = useState<CacheEntry | null>(null);

  // Synchronous cache lookup. On a hit, snap state to it in render — the
  // render-phase guarded reset pattern documented in webapp/CLAUDE.md.
  const hit = wantKey ? cacheGet(wantKey) : null;
  if (hit && last !== hit) setLast(hit);

  useEffect(() => {
    if (!wantKey || !campaignId || !cycleId || cache.has(wantKey)) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(campaignId, cycleId, ac.signal);
        if (cancelled || !name) return;
        const [preview, series] = await Promise.all([
          fetchDatasetPreview(name, 1000, ac.signal, scope, campaignId, cycleId),
          fetchMeasurementSeries(name, 1000, ac.signal, scope, campaignId, cycleId).catch(() => null),
        ]);
        if (cancelled) return;
        const entry: CacheEntry = {
          datasetName: name,
          splitTest: preview.split_test,
          slice: sliceFrom(preview.items, series?.items),
        };
        cacheSet(wantKey, entry);
        setLast(entry);
      } catch {
        /* transient fetch failure — a cycle change re-runs this */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [wantKey, campaignId, cycleId, scope]);

  const entry = hit ?? last;
  const isStale = !!wantKey && !hit;

  if (!entry) return { ...EMPTY, isStale };
  return {
    datasetName: entry.datasetName,
    splitTest: entry.splitTest,
    ...entry.slice,
    isStale,
  };
}
