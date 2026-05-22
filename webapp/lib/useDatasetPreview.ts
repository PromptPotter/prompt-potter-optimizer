"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples heat-map. One fetch chain
// (dataset name → preview → measurement series), owned here so a tab swap
// (New Job ↔ View Results) reuses the result instead of re-fetching.

import { useEffect, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "./api";

export interface DatasetPreviewState {
  datasetName: string | null;
  items: DatasetItem[];
  trainCount: number;
  testCount: number;
  archivePerSample: Map<number, MeasurementDot[]>;
}

// The bundle plus the unit key it was loaded for. The key lets a render
// between a unit switch and the new fetch landing tell its data apart from
// the prior unit's — see the return derivation below.
interface Loaded extends DatasetPreviewState {
  key: string | null;
}

const EMPTY: DatasetPreviewState = {
  datasetName: null,
  items: [],
  trainCount: 0,
  testCount: 0,
  archivePerSample: new Map(),
};

// One hook, one bundled state, one effect. `scope` is the campaign ⇄ dataset
// heat-map toggle; changing it re-fetches the measurement dots but keeps the
// same unit key, so the roster stays put while only the evidence swaps.
export function useDatasetPreview(
  campaignId: string | null,
  cycleId: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  const [loaded, setLoaded] = useState<Loaded>({ ...EMPTY, key: null });
  const unitKey = campaignId && cycleId ? `${campaignId} ${cycleId}` : null;

  useEffect(() => {
    if (!campaignId || !cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(campaignId, cycleId, ac.signal);
        if (cancelled || !name) return;
        // The sample roster is dataset-wide — fetch it at dataset scope so
        // the heat-map always renders, even on a campaign with no
        // hard_samples.json yet (campaign-scope /preview 404s).
        const preview = await fetchDatasetPreview(
          name, 1000, ac.signal, "dataset", campaignId, cycleId,
        );
        // Cell evidence follows the campaign ⇄ dataset toggle. Campaign
        // scope is empty before the campaign's first round — fall back to
        // the cross-campaign archive so the badge still shows hit/miss.
        let seriesRes = await fetchMeasurementSeries(
          name, 1000, ac.signal, scope, campaignId, cycleId,
        ).catch(() => null);
        if ((!seriesRes || seriesRes.items.length === 0) && scope !== "dataset") {
          seriesRes = await fetchMeasurementSeries(
            name, 1000, ac.signal, "dataset", campaignId, cycleId,
          ).catch(() => null);
        }
        if (cancelled) return;
        const archivePerSample = new Map<number, MeasurementDot[]>();
        for (const s of seriesRes?.items ?? []) {
          archivePerSample.set(s.sample_id, s.measurements);
        }
        setLoaded({
          key: `${campaignId} ${cycleId}`,
          datasetName: name,
          items: preview.items,
          trainCount: preview.train_count,
          testCount: preview.test_count,
          archivePerSample,
        });
      } catch {
        /* transient fetch failure — a cycle/scope change re-runs this */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [campaignId, cycleId, scope]);

  // No stale frame: until the effect has loaded data FOR THIS unit, return
  // EMPTY. A pure derivation — the loaded data carries the unit key it was
  // fetched for, so a render between a unit switch and the new fetch landing
  // can never show the prior unit's roster.
  return loaded.key === unitKey ? loaded : EMPTY;
}
