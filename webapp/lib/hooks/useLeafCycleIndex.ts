"use client";
// The VIEWED LEAF hop's index.json — its dataset name and its started-at
// timestamp, from ONE fetch. The connector / pipeline hero / hard-samples panes
// follow the leaf (like the dashboard already does), so an L4 inner loop shows
// the inner run's dataset — justlogic's `llm_only` + termnorm backend + real
// per-sample heat-map — instead of the outer pp-self meta pipeline; and the ETA
// chip's burn-rate reads the INNER cycle's start time, not the older outer root's
// (else leaf spend ÷ outer age understates the burn and overstates the ETA).
//
// At depth 1 (leaf == root) there is nothing to fetch: the caller already holds
// the root dataset name from the workspace `/cycles` list and the shell already
// fetches the root index.json for its started-at, so we return the root name and
// a null `createdAt` (caller keeps its root value) — byte-identical to today, no
// extra request. Only a genuine drill-in (path length > 1) fetches the leaf
// `index.json`; inner cycles live in an off-registry `.inner/` sandbox, so their
// header isn't in the `/cycles` list and must be read from the leaf's own file.
// `fetchCycleFileByPath` rides the same `?descend=` seam `useRoundFile` uses, so
// it resolves any depth.
//
// Same stamp discipline as `useRoundFile`: the loaded value is stamped with the
// encoded path it was fetched for and returned only once the stamp matches, so a
// drill-in never flashes the outer meta-pipeline's dataset name into the connector.

import { useEffect, useRef, useState } from "react";
import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";

export interface LeafCycleIndex {
  datasetName: string | null;
  createdAt: string | null;
}

const EMPTY: LeafCycleIndex = { datasetName: null, createdAt: null };

interface Loaded {
  key: string | null;
  value: LeafCycleIndex;
}

export function useLeafCycleIndex(
  path: CyclePath | null,
  rootDatasetName: string | null,
): LeafCycleIndex {
  // Depth 1 (or no path) — the leaf IS the root; the workspace knows its name and
  // the shell already fetches the root started-at. No fetch, no stamp, no flash.
  const isDeep = !!path && path.length > 1;
  const key = isDeep && path ? encodeCyclePath(path) : null;

  const [loaded, setLoaded] = useState<Loaded>({ key: null, value: EMPTY });
  // Keep the current path for the fetch without depending on the array's
  // per-render identity — the effect keys on the stable `key` string alone.
  const pathRef = useRef<CyclePath | null>(path);
  useEffect(() => {
    pathRef.current = path;
  });

  useEffect(() => {
    const p = pathRef.current;
    if (!key || !p) return;
    const ac = new AbortController();
    (async () => {
      try {
        const resp = await fetchCycleFileByPath(p, "cycle", "index.json", ac.signal);
        if (ac.signal.aborted) return;
        const idx = resp.content ? JSON.parse(resp.content) : {};
        const datasetName =
          (typeof idx.header?.dataset_name === "string" && idx.header.dataset_name) ||
          (typeof idx.dataset_name === "string" && idx.dataset_name) ||
          null;
        const createdAt = typeof idx.created_at === "string" ? idx.created_at : null;
        setLoaded({ key, value: { datasetName, createdAt } });
      } catch {
        if (!ac.signal.aborted) setLoaded({ key, value: EMPTY });
      }
    })();
    return () => ac.abort();
  }, [key]);

  // Depth 1: dataset name is the workspace's; `createdAt` stays null so the caller
  // keeps its root-sourced started-at.
  if (!isDeep) return { datasetName: rootDatasetName, createdAt: null };
  // Until the fetch for the CURRENT leaf lands, return empty (not the root values —
  // that would show the outer meta pipeline + outer start time for a frame).
  return loaded.key === key ? loaded.value : EMPTY;
}
