"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples heat-map. Both data scopes
// (campaign-only and cross-campaign dataset) are fetched once per unit and
// held together, so the campaign ⇄ dataset toggle is an instant in-memory
// swap — never a re-fetch, never a blank frame. The optimizer's own
// next-sample picker always runs on the dataset scope (see
// l1/execute.py round-subset fit); this toggle is display only.

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

export interface DatasetPreviewState {
  datasetName: string | null;
  // Held-out test fold size declared in datasets/{name}/campaign.json —
  // scope-independent. null when the dataset declares no split.
  splitTest: number | null;
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  archivePerSample: Map<number, MeasurementDot[]>;
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

// Per-sample dot map from a measurement-series response.
function dotMap(
  items: { sample_id: number; measurements: MeasurementDot[] }[],
): Map<number, MeasurementDot[]> {
  const m = new Map<number, MeasurementDot[]>();
  for (const s of items) m.set(s.sample_id, s.measurements);
  return m;
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

        // Dataset scope — the cross-campaign roster + Rasch sort. This is
        // the series the optimizer's picker actually follows; it always
        // resolves (the archive snapshot needs no campaign artifact).
        const dsPreview = await fetchDatasetPreview(
          name, 1000, ac.signal, "dataset", campaignId, cycleId,
        );
        const dsSeries = await fetchMeasurementSeries(
          name, 1000, ac.signal, "dataset", campaignId, cycleId,
        ).catch(() => null);

        // Campaign scope — this campaign's own pooled fit. Before the
        // campaign's first round it has measured nothing; fall back to the
        // dataset slice so a campaign that simply hasn't run yet never
        // shows an artificially-empty "fresh" sort.
        const cmpPreview = await fetchDatasetPreview(
          name, 1000, ac.signal, "campaign", campaignId, cycleId,
        ).catch(() => null);
        const cmpSeries = await fetchMeasurementSeries(
          name, 1000, ac.signal, "campaign", campaignId, cycleId,
        ).catch(() => null);
        if (cancelled) return;

        const datasetSlice: ScopeSlice = {
          items: dsPreview.items,
          measuredCount: dsPreview.measured_count,
          unmeasuredCount: dsPreview.unmeasured_count,
          archivePerSample: dotMap(dsSeries?.items ?? []),
        };
        const campaignHasData = !!cmpPreview && cmpPreview.measured_count > 0;
        const campaignSlice: ScopeSlice = campaignHasData
          ? {
              items: cmpPreview.items,
              measuredCount: cmpPreview.measured_count,
              unmeasuredCount: cmpPreview.unmeasured_count,
              archivePerSample: dotMap(
                (cmpSeries && cmpSeries.items.length > 0
                  ? cmpSeries
                  : dsSeries
                )?.items ?? [],
              ),
            }
          : datasetSlice;

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
