"use client";

import { useMemo, useState } from "react";
import {
  cellsRead,
  type CellCandidate,
  type CellRow,
  type CellsFilter,
  type CellsResponse,
  type DatasetItem,
  type HardSampleOrder,
  type HardSamplesScope,
} from "../api";
import { CELLS_LIVE_POLL_MS } from "../cadence";
import { encodeCyclePath, type CyclePath } from "../ids";
import { useMomentAt } from "../workspace";
import { useRead, type ReadFailure } from "./useRead";

export type SeriesTotals = Pick<
  CellsResponse,
  "total_measurements" | "total_hits" | "mean_fitness" | "never_hit" | "partly_hit" | "always_hit"
>;

interface ScopeSlice {
  items: DatasetItem[];
  candidates: CellCandidate[];
  cells: CellRow[];
  measuredCount: number;
  unmeasuredCount: number;
  // Null until a read lands: unread and zero-measured must not spell the same headline.
  totals: SeriesTotals | null;
}

interface CellsState extends ScopeSlice {
  order: HardSampleOrder | null;
  isStale: boolean;
  // Consumers MUST render it: a failed read and an empty slice spell `items` the same way.
  failure: ReadFailure | null;
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
  order: null,
  isStale: false,
  failure: null,
};

function sliceFrom(r: CellsResponse): ScopeSlice {
  return {
    items: r.samples,
    candidates: r.candidates,
    cells: r.cells,
    measuredCount: r.measured_samples,
    unmeasuredCount: r.unmeasured_samples,
    totals: {
      total_measurements: r.total_measurements,
      total_hits: r.total_hits,
      mean_fitness: r.mean_fitness,
      never_hit: r.never_hit,
      partly_hit: r.partly_hit,
      always_hit: r.always_hit,
    },
  };
}

export function useCells(
  path: CyclePath | null,
  datasetName: string | null,
  scope: HardSamplesScope,
  order: HardSampleOrder | null,
  live: boolean,
  filter: CellsFilter = {},
): CellsState {
  const unitKey = path ? encodeCyclePath(path) : null;

  const at = useMomentAt(path);
  const read = useRead(
    path && datasetName ? cellsRead(datasetName, path, scope, order, filter, at) : null,
    { expects: "gone", intervalMs: live ? CELLS_LIVE_POLL_MS : undefined },
  );

  const own = read.status === "ready" ? read.data : read.status === "idle" ? null : read.kept;
  // The last body this unit showed, so a slice still in flight dims it rather than blanking.
  const [shown, setShown] = useState<{ unit: string; body: CellsResponse } | null>(null);
  if (own && unitKey && shown?.body !== own) setShown({ unit: unitKey, body: own });
  const body = own ?? (shown && shown.unit === unitKey ? shown.body : null);
  const slice = useMemo(() => (body ? sliceFrom(body) : EMPTY_SLICE), [body]);

  if (!unitKey) return EMPTY;
  if (read.status === "idle") return { ...EMPTY_SLICE, order: null, isStale: true, failure: null };
  if (read.status === "loading") {
    return { ...slice, order: body?.order ?? null, isStale: true, failure: null };
  }
  if (own) return { ...slice, order: own.order, isStale: false, failure: null };
  // 404 is an honest EMPTY: no pooled slice exists before the first round closes.
  const dead = read.status === "failed" && read.failure.kind !== "gone";
  return {
    ...EMPTY_SLICE,
    order: null,
    isStale: false,
    failure: dead ? read.failure : null,
  };
}
