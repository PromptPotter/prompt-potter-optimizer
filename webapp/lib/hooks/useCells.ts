"use client";
// The measurement log for the unit in view — `GET /datasets/{name}/cells`, the sole source for
// every list of measured cells. A live unit re-reads on a poll; a stopped one reads once.

import { useMemo, useState } from "react";
import {
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
import { useRead } from "./useRead";

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

export interface CellsState extends ScopeSlice {
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

  // Parked until `datasetName` lands from its own, later read.
  const read = useRead(
    unitKey && rootCampaignId && rootCycleId && datasetName
      ? {
          key: [
            unitKey,
            datasetName,
            scope,
            order ?? "",
            candidateId ?? "",
            round ?? "",
            status ?? "",
          ].join(SEP),
          conditional: (signal, etag) =>
            fetchCells(
              datasetName,
              signal,
              etag,
              scope,
              rootCampaignId,
              rootCycleId,
              descend,
              order ?? undefined,
              { candidateId, round, status },
            ),
        }
      : null,
    { surface: "cells", expects: "gone", intervalMs: live ? LIVE_REFRESH_MS : undefined },
  );

  // A failed refresh leaves the measured rows on screen alone, hence `kept`.
  const own = read.status === "ready" ? read.data : read.status === "idle" ? null : read.kept;
  // The last body this unit showed, so a slice still in flight dims it rather than blanking.
  const [shown, setShown] = useState<{ unit: string; body: CellsResponse } | null>(null);
  if (own && unitKey && shown?.body !== own) setShown({ unit: unitKey, body: own });
  const body = own ?? (shown && shown.unit === unitKey ? shown.body : null);
  const slice = useMemo(() => (body ? sliceFrom(body) : EMPTY_SLICE), [body]);

  if (!unitKey) return EMPTY;
  if (read.status === "idle") return { ...EMPTY_SLICE, order: null, isStale: true, error: null };
  if (read.status === "loading") {
    return { ...slice, order: body?.order ?? null, isStale: true, error: null };
  }
  if (own) return { ...slice, order: own.order, isStale: false, error: null };
  // 404 is an honest EMPTY: no pooled slice exists before the first round closes.
  const dead = read.status === "failed" && read.failure.kind !== "gone";
  return {
    ...EMPTY_SLICE,
    order: null,
    isStale: false,
    error: dead ? read.failure.message : null,
  };
}
