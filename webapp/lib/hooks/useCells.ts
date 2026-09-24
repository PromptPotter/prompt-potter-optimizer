"use client";
// The measurement log for the unit in view — `GET /datasets/{name}/cells`, the sole source for
// every list of measured cells. A live unit re-reads on a poll; a stopped one reads once.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePoll } from "./usePoll";
import {
  failureKind,
  fetchCells,
  type CellCandidate,
  type CellRow,
  type CellsFilter,
  type CellsResponse,
  type DatasetItem,
  type HardSampleOrder,
  type HardSamplesScope,
} from "../api";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";

export type SeriesTotals = Pick<CellsResponse, "total_measurements" | "total_hits" | "mean_fitness">;

interface ScopeSlice {
  // Served in `hard_sample_rank` order; never re-sort.
  items: DatasetItem[];
  candidates: CellCandidate[];
  cells: CellRow[];
  measuredCount: number;
  unmeasuredCount: number;
  // Null until a read lands: unread and zero-measured must not spell the same headline.
  totals: SeriesTotals | null;
}

// `slice` null with no `error` = still in flight.
interface ScopeState {
  slice: ScopeSlice | null;
  error: string | null;
  splitTest: number | null;
  order: HardSampleOrder | null;
}

export interface CellsState extends ScopeSlice {
  splitTest: number | null;
  order: HardSampleOrder | null;
  isStale: boolean;
  // Consumers MUST render it: a failed read and an empty slice spell `items` the same way.
  error: string | null;
}

const EMPTY_SLICE: ScopeSlice = {
  items: [],
  candidates: [],
  cells: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  totals: null,
};

const EMPTY: CellsState = {
  ...EMPTY_SLICE,
  splitTest: null,
  order: null,
  isStale: false,
  error: null,
};

function sliceFrom(r: CellsResponse): ScopeSlice {
  const measured = r.samples.filter((it) => it.n_measured > 0).length;
  return {
    items: r.samples,
    candidates: r.candidates,
    cells: r.cells,
    measuredCount: measured,
    unmeasuredCount: r.samples.length - measured,
    totals: {
      total_measurements: r.total_measurements,
      total_hits: r.total_hits,
      mean_fitness: r.mean_fitness,
    },
  };
}

const SEP = "\u001f";

type SliceKey = string;

function keepUnit(
  slices: Record<SliceKey, ScopeState>,
  unitKey: string,
): Record<SliceKey, ScopeState> {
  const prefix = `${unitKey}${SEP}`;
  const out: Record<SliceKey, ScopeState> = {};
  for (const [k, v] of Object.entries(slices)) if (k.startsWith(prefix)) out[k] = v;
  return out;
}

// The route carries no validator, so every tick is a full body.
const LIVE_REFRESH_MS = 8000;

export function useCells(
  path: CyclePath | null,
  datasetName: string | null,
  scope: HardSamplesScope,
  // Null = no override; the server resolves the dataset's declared key and echoes it on `order`.
  order: HardSampleOrder | null,
  live: boolean,
  filter: CellsFilter = {},
): CellsState {
  const { candidateId, round, status } = filter;
  // ROOT hop + `descend` tail, so an L4 inner drill-in reads the inner sandbox.
  const root = path ? pathRoot(path) : null;
  const rootCampaignId = root?.campaignId ?? null;
  const rootCycleId = root?.cycleId ?? null;
  const descend = path ? encodeDescend(path) : "";
  const unitKey = path ? encodeCyclePath(path) : null;
  const sliceKey: SliceKey | null = unitKey
    ? [unitKey, scope, order ?? "", candidateId ?? "", round ?? "", status ?? ""].join(SEP)
    : null;
  // ONE value guards both the claim and the fetch: `datasetName` lands from a LATER read, and a
  // claim made before it would refuse the retry that arrives with it.
  const req = useMemo(
    () =>
      sliceKey && unitKey && rootCampaignId && rootCycleId && datasetName
        ? { key: sliceKey, unit: unitKey, rootCampaignId, rootCycleId, datasetName }
        : null,
    [sliceKey, unitKey, rootCampaignId, rootCycleId, datasetName],
  );

  const [slices, setSlices] = useState<Record<SliceKey, ScopeState>>({});
  // A ref, not state: it must not re-run the effect that writes it.
  const started = useRef<Set<SliceKey>>(new Set());

  // `seeding` decides FAILURE only: an empty slice reports why; a failed refresh leaves the
  // measured rows on screen alone.
  const load = useCallback(
    async (signal: AbortSignal, seeding: boolean) => {
      if (!req) return;
      const { key, unit, rootCampaignId, rootCycleId, datasetName } = req;
      try {
        const r = await fetchCells(
          datasetName,
          signal,
          scope,
          rootCampaignId,
          rootCycleId,
          descend,
          order ?? undefined,
          { candidateId, round, status },
        );
        // The MARK is released by the effect's cleanup, never here.
        if (signal.aborted) return;
        setSlices((prev) => ({
          ...keepUnit(prev, unit),
          [key]: { slice: sliceFrom(r), error: null, splitTest: r.split_test, order: r.order },
        }));
      } catch (e) {
        if (signal.aborted || !seeding) return;
        // 404 is an honest EMPTY: no pooled slice exists before the first round closes.
        const gone = failureKind(e) === "gone";
        setSlices((prev) => ({
          ...keepUnit(prev, unit),
          [key]: {
            slice: gone ? EMPTY_SLICE : null,
            error: gone ? null : e instanceof Error ? e.message : String(e),
            splitTest: null,
            order: null,
          },
        }));
      }
    },
    [req, descend, scope, order, candidateId, round, status],
  );

  useEffect(() => {
    if (!req) return;
    const claims = started.current;
    const prefix = `${req.unit}${SEP}`;
    for (const k of [...claims]) if (!k.startsWith(prefix)) claims.delete(k);
    if (claims.has(req.key)) return;
    claims.add(req.key);
    const ac = new AbortController();
    // Released HERE: cleanup precedes the next effect body, while a release inside the aborted
    // `load` lands a microtask late and strands the slice "loading" for the life of the tab.
    let settled = false;
    void load(ac.signal, true).then(
      () => {
        settled = true;
      },
      () => claims.delete(req.key),
    );
    return () => {
      ac.abort();
      if (!settled) claims.delete(req.key);
    };
  }, [req, load]);

  usePoll((signal) => load(signal, false), {
    intervalMs: LIVE_REFRESH_MS,
    enabled: live && req !== null,
  });

  if (!sliceKey) return EMPTY;
  if (!req) return { ...EMPTY_SLICE, splitTest: null, order: null, isStale: true, error: null };
  const state = slices[sliceKey];

  // In flight: show any slice held for this unit, marked stale, rather than blank the panel.
  if (!state || (!state.slice && !state.error)) {
    const sibling = unitKey
      ? Object.entries(slices).find(([k, v]) => k.startsWith(`${unitKey}${SEP}`) && v.slice)?.[1]
      : undefined;
    return {
      ...(sibling?.slice ?? EMPTY_SLICE),
      splitTest: sibling?.splitTest ?? null,
      order: sibling?.order ?? null,
      isStale: true,
      error: null,
    };
  }

  return {
    ...(state.slice ?? EMPTY_SLICE),
    splitTest: state.splitTest,
    order: state.order,
    isStale: false,
    error: state.error,
  };
}
