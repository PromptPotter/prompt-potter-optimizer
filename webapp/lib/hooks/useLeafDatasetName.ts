"use client";
// The dataset name of the VIEWED LEAF hop. The connector / pipeline hero /
// hard-samples panes follow the leaf (like the dashboard already does), so an
// L4 inner loop shows the inner run's dataset — justlogic's `llm_only` + termnorm
// backend + real per-sample heat-map — instead of the outer pp-self meta pipeline.
//
// At depth 1 (leaf == root) there is nothing to fetch: the caller already holds
// the root dataset name from the workspace `/cycles` list, so we return it
// verbatim — byte-identical to today, no extra request. Only a genuine drill-in
// (path length > 1) fetches the leaf `index.json` header; inner cycles live in an
// off-registry `.inner/` sandbox, so their dataset name isn't in the `/cycles`
// list and must be read from the leaf's own file. `fetchCycleFileByPath` rides
// the same `?descend=` seam `useRoundFile` uses, so it resolves any depth.
//
// Same stamp discipline as `useRoundFile`: the loaded name is stamped with the
// encoded path it was fetched for and returned only once the stamp matches, so a
// drill-in never flashes the outer meta-pipeline's dataset name into the connector.

import { useEffect, useRef, useState } from "react";
import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";

interface Loaded {
  key: string | null;
  name: string | null;
}

export function useLeafDatasetName(
  path: CyclePath | null,
  rootDatasetName: string | null,
): string | null {
  // Depth 1 (or no path) — the leaf IS the root; the workspace already knows its
  // name. No fetch, no stamp, no flash.
  const isDeep = !!path && path.length > 1;
  const key = isDeep && path ? encodeCyclePath(path) : null;

  const [loaded, setLoaded] = useState<Loaded>({ key: null, name: null });
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
        const name =
          (typeof idx.header?.dataset_name === "string" && idx.header.dataset_name) ||
          (typeof idx.dataset_name === "string" && idx.dataset_name) ||
          null;
        setLoaded({ key, name });
      } catch {
        if (!ac.signal.aborted) setLoaded({ key, name: null });
      }
    })();
    return () => ac.abort();
  }, [key]);

  if (!isDeep) return rootDatasetName;
  // Until the fetch for the CURRENT leaf lands, return null (not the root name —
  // that would show the outer meta pipeline for a frame). The connector's own
  // render-phase reset handles the brief empty window.
  return loaded.key === key ? loaded.name : null;
}
