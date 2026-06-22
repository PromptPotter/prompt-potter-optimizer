"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples table. One fetch chain per
// (campaignId, cycleId) UNIT loads BOTH scope slices (campaign-pooled +
// cross-campaign dataset) into one unit-stamped state object; the scope toggle
// is a pure in-memory pick between the two already-loaded slices — no re-fetch,
// no cross-scope borrow, so the toggle can never show one scope's data while
// pointed at the other. A unit switch shows the prior unit's slice marked
// `isStale` until the new fetch lands (never blanks); a failed dataset fetch
// surfaces honestly via `error` rather than silently showing stale data.

import { useEffect, useState } from "react";
import {
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "../api";

interface ScopeSlice {
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  archivePerSample: Map<number, MeasurementDot[]>;
}

export interface DatasetPreviewState extends ScopeSlice {
  splitTest: number | null;
  isStale: boolean;
  // Honest failure signal — set when the dataset-scope fetch (the spine)
  // threw for the unit in view. Additive: consumers may ignore it (the table
  // already renders an empty state for an empty slice).
  error: string | null;
}

interface Loaded {
  // Unit stamp the slices were fetched for; null until the first load lands.
  key: string | null;
  splitTest: number | null;
  campaign: ScopeSlice;
  dataset: ScopeSlice;
  error: string | null;
}

const EMPTY_SLICE: ScopeSlice = {
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  archivePerSample: new Map(),
};

const EMPTY: DatasetPreviewState = {
  splitTest: null,
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  archivePerSample: new Map(),
  isStale: false,
  error: null,
};

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
  datasetName: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  // \x1f (unit separator) can't collide with id characters.
  const unitKey = campaignId && cycleId ? `${campaignId}\x1f${cycleId}` : null;

  const [loaded, setLoaded] = useState<Loaded>({
    key: null,
    splitTest: null,
    campaign: EMPTY_SLICE,
    dataset: EMPTY_SLICE,
    error: null,
  });

  useEffect(() => {
    if (!unitKey || !campaignId || !cycleId || !datasetName) return;
    const name = datasetName;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        // Both scopes load together. The dataset preview is the spine — a real
        // failure throws into the catch below (honest `error`). The campaign
        // slice + both series are floored to null: a fresh campaign legitimately
        // has no campaign-scope artifact yet (honest empty, never borrowed).
        const [dsPreview, dsSeries, cmpPreview, cmpSeries] = await Promise.all([
          fetchDatasetPreview(name, 1000, ac.signal, "dataset", campaignId, cycleId),
          fetchMeasurementSeries(name, 1000, ac.signal, "dataset", campaignId, cycleId).catch(
            () => null,
          ),
          fetchDatasetPreview(name, 1000, ac.signal, "campaign", campaignId, cycleId).catch(
            () => null,
          ),
          fetchMeasurementSeries(name, 1000, ac.signal, "campaign", campaignId, cycleId).catch(
            () => null,
          ),
        ]);
        if (cancelled) return;
        setLoaded({
          key: unitKey,
          splitTest: dsPreview.split_test,
          dataset: sliceFrom(dsPreview.items, dsSeries?.items),
          campaign: cmpPreview
            ? sliceFrom(cmpPreview.items, cmpSeries?.items)
            : EMPTY_SLICE,
          error: null,
        });
      } catch (e) {
        if (cancelled || ac.signal.aborted) return;
        setLoaded({
          key: unitKey,
          splitTest: null,
          campaign: EMPTY_SLICE,
          dataset: EMPTY_SLICE,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [unitKey, campaignId, cycleId, datasetName]);

  if (!unitKey) return EMPTY;
  // First load for this session — show a loading affordance, not blank chrome.
  if (loaded.key === null) return { ...EMPTY, isStale: true };

  // Pure pick AFTER the unit check: the returned slice is always the requested
  // scope's slice for whatever unit `loaded` holds — fresh or stale-pending-new
  // — so it is structurally impossible to show campaign data while scope is
  // dataset.
  const fresh = loaded.key === unitKey;
  const slice = scope === "campaign" ? loaded.campaign : loaded.dataset;
  return {
    splitTest: loaded.splitTest,
    ...slice,
    isStale: !fresh,
    error: fresh ? loaded.error : null,
  };
}
