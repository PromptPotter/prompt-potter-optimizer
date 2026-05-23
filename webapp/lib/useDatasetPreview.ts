"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples table. Both data scopes
// (campaign-only and cross-campaign dataset) are fetched once per unit and
// held together, so the campaign ⇄ dataset toggle is an instant in-memory
// swap — never a re-fetch, never a blank frame. The optimizer's own
// next-sample picker always runs on the dataset scope (see
// l1/execute.py round-subset fit); this toggle is display only.
//
// Each scope is purely itself: the campaign slice carries this campaign's
// measured/unmeasured counts + this campaign's per-sample dots, full stop.
// No fallback to the dataset slice — that would let dataset-wide numbers
// leak into "This campaign" mode, which is the opposite of what the toggle
// is for.

import { useEffect, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "./api";

// One scope's roster + measurement evidence. Held twice (campaign,
// dataset) inside the hook; the toggle picks which one surfaces.
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
}

// Both scopes plus the unit key they were loaded for. The key lets a
// render between a unit switch and the new fetch landing tell its data
// apart from the prior unit's — see the return derivation below.
interface Loaded {
  key: string | null;
  datasetName: string | null;
  splitTest: number | null;
  campaign: ScopeSlice;
  dataset: ScopeSlice;
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
};

function dotMap(
  items: { sample_id: number; measurements: MeasurementDot[] }[] | undefined,
): Map<number, MeasurementDot[]> {
  const m = new Map<number, MeasurementDot[]>();
  if (items) for (const s of items) m.set(s.sample_id, s.measurements);
  return m;
}

// Build a scope slice with counts derived from the dot data, not the
// preview endpoint. "Measured" = at least one dot in the History column —
// same signal the "Hide unmeasured" tick uses, so the footer never lies
// about what the tick would hide.
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

// One hook, both scopes, one effect. The effect fires per unit (not per
// scope) — `scope` only drives the pure return derivation, so toggling it
// never re-runs the fetch.
export function useDatasetPreview(
  campaignId: string | null,
  cycleId: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  const [loaded, setLoaded] = useState<Loaded>({
    key: null,
    datasetName: null,
    splitTest: null,
    campaign: EMPTY_SLICE,
    dataset: EMPTY_SLICE,
  });
  const unitKey = campaignId && cycleId ? `${campaignId} ${cycleId}` : null;

  useEffect(() => {
    if (!campaignId || !cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(campaignId, cycleId, ac.signal);
        if (cancelled || !name) return;
        // Four independent fetches: dataset + campaign × preview + series.
        // Run in parallel; campaign-scope fetches may 404 for a fresh
        // campaign with no `hard_samples.json` yet — their `.catch` floors
        // them to null and the empty slice falls out cleanly.
        const [dsPreview, dsSeries, cmpPreview, cmpSeries] = await Promise.all([
          fetchDatasetPreview(name, 1000, ac.signal, "dataset", campaignId, cycleId),
          fetchMeasurementSeries(name, 1000, ac.signal, "dataset", campaignId, cycleId).catch(() => null),
          fetchDatasetPreview(name, 1000, ac.signal, "campaign", campaignId, cycleId).catch(() => null),
          fetchMeasurementSeries(name, 1000, ac.signal, "campaign", campaignId, cycleId).catch(() => null),
        ]);
        if (cancelled) return;

        // Counts come from the same signal the "Hide unmeasured" tick
        // uses — presence of an actual measurement dot — so the footer
        // and the filter never disagree about what "measured" means.
        const datasetSlice = sliceFrom(dsPreview.items, dsSeries?.items);
        const campaignSlice = cmpPreview
          ? sliceFrom(cmpPreview.items, cmpSeries?.items)
          : EMPTY_SLICE;

        setLoaded({
          key: `${campaignId} ${cycleId}`,
          datasetName: name,
          splitTest: dsPreview.split_test,
          campaign: campaignSlice,
          dataset: datasetSlice,
        });
      } catch {
        /* transient fetch failure — a cycle change re-runs this */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [campaignId, cycleId]);

  // No stale frame: until the effect has loaded data FOR THIS unit, return
  // EMPTY. A pure derivation — the toggle picks a held slice, so switching
  // scope can never show the prior unit's roster and never re-fetches.
  if (loaded.key !== unitKey) return EMPTY;
  const slice = scope === "campaign" ? loaded.campaign : loaded.dataset;
  return {
    datasetName: loaded.datasetName,
    splitTest: loaded.splitTest,
    ...slice,
  };
}
