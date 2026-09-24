"use client";
// The measurement log for the unit in view — `GET /datasets/{name}/cells`: the ranked sample
// roster, the candidates that measured it and every cell between them, in ONE read. Grouped
// by sample it is the hard-sample leaderboard; the Measurements view buckets the same rows by
// candidate or not at all (`domain/cells.py`).
//
// **One slice is fetched at a time: the one in view.** Each (unit, scope, order, filter) is
// fetched once and kept, so flipping a control back is a pure in-memory pick and no slice is
// ever borrowed across keys silently.
//
// A unit switch shows the prior unit's slice marked `isStale` until the new fetch lands (never
// blanks); a failed read surfaces honestly via `error` rather than silently reading as an
// empty roster.
//
// **Once is not enough while the unit is LIVE.** A run measuring cells right now would decorate
// its roster with whatever was banked at mount — nothing at all through round 0 — until a
// remount. A live unit re-reads on the same poll shape the tree uses; a stopped one reads once,
// because nothing under it can change.

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

// The roster-wide outcome numbers, straight off `CellsResponse` — a projection of the wire
// type, never a second declaration of it.
export type SeriesTotals = Pick<CellsResponse, "total_measurements" | "total_hits" | "mean_fitness">;

interface ScopeSlice {
  // The ranked roster — `items[i]` is served in `hard_sample_rank` order, and each row carries
  // its own served aggregates (`n_measured`, `n_hits`, `mean_fitness`).
  items: DatasetItem[];
  candidates: CellCandidate[];
  cells: CellRow[];
  measuredCount: number;
  unmeasuredCount: number;
  // Null until a read lands — an unread scope and a scope with zero measurements are
  // different facts, and the roster headline must not spell them the same way.
  totals: SeriesTotals | null;
}

// One slice's outcome. `slice` null with no `error` means the read is still in flight; the
// three states (loading / failed / empty) are distinguishable by construction.
interface ScopeState {
  slice: ScopeSlice | null;
  error: string | null;
  splitTest: number | null;
  // The key the server ranked `items` by, off its echo. `null` until a read lands.
  order: HardSampleOrder | null;
}

export interface CellsState extends ScopeSlice {
  splitTest: number | null;
  order: HardSampleOrder | null;
  isStale: boolean;
  // Set when the read for the slice IN VIEW failed. Consumers MUST render it: an empty slice
  // and a failed read are different facts that `items` spells the same way.
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

// "Measured" = at least one graded cell in scope — the served `n_measured`, so the footer
// never lies about what "hide unmeasured" would hide.
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

// One entry per slice actually fetched. `order` and the filter are dimensions, not re-sorts:
// the ranking is the server's (webapp/CLAUDE.md § Scoring authority).
type SliceKey = string;

// Keep only the unit in view. A roster is up to 1000 rows plus its cells, so a session that
// browses ten campaigns would otherwise hold ten of them alive for no reader.
function keepUnit(
  slices: Record<SliceKey, ScopeState>,
  unitKey: string,
): Record<SliceKey, ScopeState> {
  const prefix = `${unitKey}${SEP}`;
  const out: Record<SliceKey, ScopeState> = {};
  for (const [k, v] of Object.entries(slices)) if (k.startsWith(prefix)) out[k] = v;
  return out;
}

// How often a LIVE unit's slice is re-read. The route carries no validator, so every tick is a
// full body — and it is not worth reading more often than a cell lands, which is tens of
// seconds on every connector we ship.
const LIVE_REFRESH_MS = 8000;

export function useCells(
  path: CyclePath | null,
  datasetName: string | null,
  scope: HardSamplesScope,
  // Null = send no override and let the server resolve the dataset's declared key. The
  // resolved value comes back on `order`, so the caller never has to know the default.
  order: HardSampleOrder | null,
  // Whether the unit in view is still measuring. Only then is a re-read news.
  live: boolean,
  // A preset's server-side narrowing; each field is part of the slice key.
  filter: CellsFilter = {},
): CellsState {
  const { candidateId, round, status } = filter;
  // The scope artifact follows the VIEWED LEAF: the request addresses the ROOT hop + a
  // `descend` tail (like the dashboard), so an L4 inner drill-in reads the inner sandbox.
  const root = path ? pathRoot(path) : null;
  const rootCampaignId = root?.campaignId ?? null;
  const rootCycleId = root?.cycleId ?? null;
  const descend = path ? encodeDescend(path) : "";
  const unitKey = path ? encodeCyclePath(path) : null;
  const sliceKey: SliceKey | null = unitKey
    ? [unitKey, scope, order ?? "", candidateId ?? "", round ?? "", status ?? ""].join(SEP)
    : null;
  // ONE object claims a slice and fills it, and `null` is the whole of "not addressable yet":
  // `datasetName` comes from a LATER read than the address does, and a claim made before it
  // lands must not refuse the retry that arrives with it.
  const req = useMemo(
    () =>
      sliceKey && unitKey && rootCampaignId && rootCycleId && datasetName
        ? { key: sliceKey, unit: unitKey, rootCampaignId, rootCycleId, datasetName }
        : null,
    [sliceKey, unitKey, rootCampaignId, rootCycleId, datasetName],
  );

  const [slices, setSlices] = useState<Record<SliceKey, ScopeState>>({});
  // Slices already fetched or in flight. A ref, not state: it must not re-run the effect
  // that writes it.
  const started = useRef<Set<SliceKey>>(new Set());

  // The one read, shared by the first fetch and the live re-read. `seeding` is about FAILURE:
  // a slice with nothing in it yet must report why, while a refresh that fails leaves the rows
  // already on screen alone — they were measured, and a failed poll is not news about them.
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
        // Dropped, and the MARK is released by the effect's cleanup rather than here.
        if (signal.aborted) return;
        setSlices((prev) => ({
          ...keepUnit(prev, unit),
          [key]: { slice: sliceFrom(r), error: null, splitTest: r.split_test, order: r.order },
        }));
      } catch (e) {
        if (signal.aborted || !seeding) return;
        // A scope whose artifact does not exist yet answers 404, and that is an honest EMPTY,
        // not a failure: a campaign has no pooled slice before its first round closes.
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
    // Forget attempts for units no longer in view, so navigating back re-fetches.
    const prefix = `${req.unit}${SEP}`;
    for (const k of [...claims]) if (!k.startsWith(prefix)) claims.delete(k);
    if (claims.has(req.key)) return;
    claims.add(req.key);
    const ac = new AbortController();
    // The mark is released HERE, in the cleanup: React runs cleanup before the next effect
    // body, so a re-run that keeps the same `key` finds the mark gone and re-claims. Released
    // from inside the aborted `load` it would land a microtask LATER, leaving the slice with
    // neither a fetch in flight nor a claim — "loading" for the life of the tab.
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

  // The live re-read. Only the slice in view, and only while the unit is measuring.
  usePoll((signal) => load(signal, false), {
    intervalMs: LIVE_REFRESH_MS,
    enabled: live && req !== null,
  });

  if (!sliceKey) return EMPTY;
  // Addressed, but not yet resolvable — the dataset name has not landed.
  if (!req) return { ...EMPTY_SLICE, splitTest: null, order: null, isStale: true, error: null };
  const state = slices[sliceKey];

  // Still in flight: fall back to ANY slice already held for this unit, marked stale, so a
  // control flip shows the neighbouring rows greyed rather than blanking the panel.
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
