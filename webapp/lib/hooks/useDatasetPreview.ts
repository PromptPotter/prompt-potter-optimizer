"use client";
// Dataset preview for the unit in view — the sample roster + per-sample
// measurement history that back the hard-samples table.
//
// **One scope is fetched at a time: the one in view.** Each (unit, scope) pair is
// fetched once and kept, so flipping the toggle back is still a pure in-memory pick
// and no slice is ever borrowed across scopes. What changed is that the OTHER
// scope's two reads no longer gate the first paint: the panel used to await all four
// `limit=1000` requests in one `Promise.all` before any state landed, so opening a
// campaign meant staring at an empty roster for as long as the SLOWEST of four reads
// — most of it spent on the cross-campaign archive, which the operator had not asked
// to see. The default scope now paints on its own two reads.
//
// A unit switch shows the prior unit's slice marked `isStale` until the new fetch
// lands (never blanks); a failed read surfaces honestly via `error` rather than
// silently reading as an empty roster.

import { useEffect, useRef, useState } from "react";
import {
  failureKind,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementSeriesResponse,
  type SampleSeries,
} from "../api";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";

// The roster-wide outcome numbers, straight off `MeasurementSeriesResponse` — a
// projection of the wire type, never a second declaration of it.
export type SeriesTotals = Pick<
  MeasurementSeriesResponse,
  "total_measurements" | "total_hits" | "mean_fitness"
>;

interface ScopeSlice {
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  // Keyed by sample_id, holding the SERIES — dots and the served aggregates over
  // exactly those dots, which is why they travel as one object rather than a dot
  // list beside a tally that could answer for some other set.
  archivePerSample: Map<number, SampleSeries>;
  // Served totals over this scope's series. Null when the series read failed or
  // returned nothing — an unread scope and a scope with zero measurements are
  // different facts, and the roster headline must not spell them the same way.
  totals: SeriesTotals | null;
}

// One scope's outcome. `slice` null with no `error` means the read is still in
// flight; the three states (loading / failed / empty) are distinguishable by
// construction, because collapsing them is what let a slow read render as "this
// dataset has no samples".
interface ScopeState {
  slice: ScopeSlice | null;
  error: string | null;
  splitTest: number | null;
}

interface DatasetPreviewState extends ScopeSlice {
  splitTest: number | null;
  isStale: boolean;
  // Honest failure signal — set when the read for the SCOPE IN VIEW failed for the
  // unit in view. Consumers MUST render it: an empty slice and a failed read are
  // different facts, and the heatmap's own guard cannot tell them apart from
  // `items` alone.
  error: string | null;
}

const EMPTY_SLICE: ScopeSlice = {
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  archivePerSample: new Map(),
  totals: null,
};

const EMPTY: DatasetPreviewState = {
  ...EMPTY_SLICE,
  splitTest: null,
  isStale: false,
  error: null,
};

function seriesMap(series: MeasurementSeriesResponse | null): Map<number, SampleSeries> {
  const m = new Map<number, SampleSeries>();
  if (series) for (const s of series.items) m.set(s.sample_id, s);
  return m;
}

// "Measured" = at least one dot in the History column — same signal the
// "Hide unmeasured" tick uses, so the footer never lies about what the tick
// would hide.
function sliceFrom(
  items: DatasetItem[],
  series: MeasurementSeriesResponse | null,
): ScopeSlice {
  const archivePerSample = seriesMap(series);
  let measured = 0;
  for (const it of items) {
    if ((archivePerSample.get(it.sample_id)?.measurements.length ?? 0) > 0) measured += 1;
  }
  return {
    items,
    measuredCount: measured,
    unmeasuredCount: items.length - measured,
    archivePerSample,
    totals: series
      ? {
          total_measurements: series.total_measurements,
          total_hits: series.total_hits,
          mean_fitness: series.mean_fitness,
        }
      : null,
  };
}

// `${unitKey}\x1f${scope}` — one entry per slice actually fetched.
type SliceKey = string;

// Keep only the unit in view. A roster is up to 1000 rows plus its series, so a
// session that browses ten campaigns would otherwise hold ten of them alive for no
// reader. Applied inside the writes rather than as its own effect — pruning is free
// there, and an extra render to throw data away is a poor trade.
function keepUnit(
  slices: Record<SliceKey, ScopeState>,
  unitKey: string,
): Record<SliceKey, ScopeState> {
  const prefix = `${unitKey}\x1f`;
  const out: Record<SliceKey, ScopeState> = {};
  for (const [k, v] of Object.entries(slices)) if (k.startsWith(prefix)) out[k] = v;
  return out;
}

export function useDatasetPreview(
  path: CyclePath | null,
  datasetName: string | null,
  scope: HardSamplesScope,
): DatasetPreviewState {
  // The scope artifact follows the VIEWED LEAF: the request addresses the ROOT
  // hop + a `descend` tail (like the dashboard), so an L4 inner drill-in reads
  // the inner sandbox's hard-samples, not the outer archive. The unit stamp is
  // the whole encoded path, so drilling into/out of an inner loop re-fetches.
  const root = path ? pathRoot(path) : null;
  const rootCampaignId = root?.campaignId ?? null;
  const rootCycleId = root?.cycleId ?? null;
  const descend = path ? encodeDescend(path) : "";
  const unitKey = path ? encodeCyclePath(path) : null;
  const sliceKey: SliceKey | null = unitKey ? `${unitKey}\x1f${scope}` : null;

  const [slices, setSlices] = useState<Record<SliceKey, ScopeState>>({});
  // Slices already fetched or in flight. A ref, not state: it must not re-run the
  // effect that writes it, and re-fetching a slice already held would defeat the
  // point of keeping them.
  const started = useRef<Set<SliceKey>>(new Set());

  useEffect(() => {
    if (!sliceKey || !unitKey || !rootCampaignId || !rootCycleId || !datasetName) return;
    // Forget attempts for units no longer in view, so navigating back re-fetches
    // rather than waiting on a "done" mark for data that has since been pruned.
    const prefix = `${unitKey}\x1f`;
    for (const k of [...started.current]) if (!k.startsWith(prefix)) started.current.delete(k);
    if (started.current.has(sliceKey)) return;
    started.current.add(sliceKey);
    const name = datasetName;
    const cmp = rootCampaignId;
    const cyc = rootCycleId;
    const key = sliceKey;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        // The roster is the spine — its failure IS this scope's failure. The series
        // is best-effort: it decorates a roster rather than being one, so a roster
        // that arrived still renders with an honest "no measurements" column.
        const [preview, series] = await Promise.all([
          fetchDatasetPreview(name, 1000, ac.signal, scope, cmp, cyc, descend),
          fetchMeasurementSeries(name, 1000, ac.signal, scope, cmp, cyc, descend).catch(
            () => null,
          ),
        ]);
        if (cancelled) return;
        setSlices((prev) => ({
          ...keepUnit(prev, unitKey),
          [key]: { slice: sliceFrom(preview.items, series), error: null, splitTest: preview.split_test },
        }));
      } catch (e) {
        if (cancelled || ac.signal.aborted) {
          // Never leave an aborted attempt marked as done — the next mount of this
          // same slice must be free to try again.
          started.current.delete(key);
          return;
        }
        // A scope whose artifact does not exist yet answers 404, and that is an
        // honest EMPTY, not a failure: a campaign legitimately has no pooled slice
        // before its first round closes. Every other failure is recorded.
        const gone = failureKind(e) === "gone";
        setSlices((prev) => ({
          ...keepUnit(prev, unitKey),
          [key]: {
            slice: gone ? EMPTY_SLICE : null,
            error: gone ? null : e instanceof Error ? e.message : String(e),
            splitTest: null,
          },
        }));
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [sliceKey, unitKey, rootCampaignId, rootCycleId, descend, datasetName, scope]);

  if (!sliceKey) return EMPTY;
  const state = slices[sliceKey];

  // Still in flight for the requested (unit, scope). Fall back to ANY slice already
  // held for this unit — the other scope's, if it is loaded — marked stale, so a
  // toggle flip shows the neighbouring numbers greyed rather than blanking the
  // panel. Nothing is borrowed silently: `isStale` is the caller's signal that what
  // is on screen does not yet answer for the scope they asked for.
  if (!state || (!state.slice && !state.error)) {
    const sibling = unitKey
      ? Object.entries(slices).find(([k, v]) => k.startsWith(`${unitKey}\x1f`) && v.slice)?.[1]
      : undefined;
    return {
      ...(sibling?.slice ?? EMPTY_SLICE),
      splitTest: sibling?.splitTest ?? null,
      isStale: true,
      error: null,
    };
  }

  return {
    ...(state.slice ?? EMPTY_SLICE),
    splitTest: state.splitTest,
    isStale: false,
    error: state.error,
  };
}
