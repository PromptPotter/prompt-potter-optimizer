"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples table.
//
// Cache shape: a process-local LRU keyed on (campaignId, cycleId, scope).
// Switching campaigns or scopes that the operator has visited recently
// hits the cache and snaps the table instantly; first visit pays one
// network round-trip but the prior unit's data stays on screen marked
// stale (`isStale: true`) so the table never blanks. Each scope is
// purely itself: the campaign slice carries this campaign's
// measured/unmeasured counts + this campaign's per-sample dots; no
// fallback to the dataset slice — that would let dataset-wide numbers
// leak into "This campaign" mode, which is the opposite of what the
// toggle is for. The optimizer's own next-sample picker always runs on
// the dataset scope (see l1/execute.py round-subset fit); this toggle
// is display only.

import { useEffect, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "./api";

// One scope's roster + measurement evidence.
interface ScopeSlice {
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  archivePerSample: Map<number, MeasurementDot[]>;
}

export interface DatasetPreviewState extends ScopeSlice {
  datasetName: string | null;
  // Held-out test fold size declared in datasets/{name}/campaign.json —
  // scope-independent. null when the dataset declares no split.
  splitTest: number | null;
  // Displayed data does not match the current (campaignId, cycleId, scope)
  // — a fetch is in flight and the table is showing the prior slice. Lets
  // the caller dim or annotate the table without ever blanking it.
  isStale: boolean;
}

interface CacheEntry {
  datasetName: string;
  splitTest: number | null;
  slice: ScopeSlice;
}

const EMPTY_SLICE: ScopeSlice = {
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  archivePerSample: new Map(),
};

const EMPTY: DatasetPreviewState = {
  datasetName: null,
  splitTest: null,
  ...EMPTY_SLICE,
  isStale: false,
};

function cacheKey(campaignId: string, cycleId: string, scope: HardSamplesScope): string {
  return `${campaignId}::${cycleId}::${scope}`;
}

// Module-level LRU. ~12 entries is enough to keep recently-visited unit ×
// scope pairs warm without holding stale data for the lifetime of the tab.
// Map preserves insertion order; we read-touch by delete+set on every hit
// and evict the oldest on overflow.
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

// One hook, one scope at a time. The effect fires on every (unit, scope)
// change — but cache hits short-circuit synchronously in render so a
// recently-visited unit never blanks the table.
export function useDatasetPreview(
  campaignId: string | null,
  cycleId: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  const wantKey =
    campaignId && cycleId ? cacheKey(campaignId, cycleId, scope) : null;

  // Most-recently-displayed entry. Held independently of the cache so a
  // cache eviction doesn't suddenly blank the table; only a fresh unit
  // switch can change what we show, and even then we keep this slot as the
  // stale-fallback until the new fetch lands.
  const [shown, setShown] = useState<{ key: string; entry: CacheEntry } | null>(
    null,
  );

  // Synchronous cache lookup. On a hit, snap state to it in render — the
  // render-phase guarded reset pattern documented in webapp/CLAUDE.md.
  const hit = wantKey ? cacheGet(wantKey) : null;
  if (hit && wantKey && (!shown || shown.key !== wantKey)) {
    setShown({ key: wantKey, entry: hit });
  }

  useEffect(() => {
    if (!wantKey || !campaignId || !cycleId) return;
    if (cache.has(wantKey)) return; // render-phase already swapped to it
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
        setShown({ key: wantKey, entry });
      } catch {
        /* transient fetch failure — a cycle change re-runs this */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [wantKey, campaignId, cycleId, scope]);

  // Resolve what to show this frame. Cache hit wins; otherwise fall through
  // to the stale state slot. Both stale and EMPTY paths report isStale so a
  // consumer can dim the table or annotate the footer.
  const entry = hit ?? shown?.entry ?? null;
  const isStale = !!wantKey && (!hit && (shown?.key ?? null) !== wantKey);

  if (!entry) {
    return { ...EMPTY, isStale: !!wantKey };
  }
  return {
    datasetName: entry.datasetName,
    splitTest: entry.splitTest,
    ...entry.slice,
    isStale,
  };
}
