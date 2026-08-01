"use client";
// The VIEWED LEAF hop's dataset name + started-at timestamp. The connector /
// pipeline hero / hard-samples panes follow the leaf (like the dashboard
// already does), so an L4 inner loop shows the inner run's dataset —
// justlogic's `llm_only` + termnorm backend + real per-sample heat-map —
// instead of the outer pp-self outer pipeline; and the ETA chip's burn-rate
// reads the INNER cycle's start time, not the older outer root's (else leaf
// spend ÷ outer age understates the burn and overstates the ETA).
//
// At depth 1 (leaf == root) there is nothing to fetch: the caller already holds
// the root dataset name from the workspace `/cycles` list and the shell already
// fetches the root index.json for its started-at, so we return the root name and
// a null `createdAt` (caller keeps its root value) — byte-identical to today, no
// extra request. Only a genuine drill-in (path length > 1) fetches the leaf's
// own files; inner cycles live in an off-registry `.inner/` sandbox, so they
// aren't in the `/cycles` list. The dataset name reads its one owner —
// `campaign.json::dataset_name` (scope=campaign) — and the started-at reads the
// leaf `index.json`; both ride the same `?descend=` seam `useRoundFile` uses.
//
// Same stamp discipline as `useRoundFile` (via the shared `usePathKeyedFetch`):
// the loaded value is returned only once its stamp matches, so a drill-in never
// flashes the outer outer pipeline's dataset name into the connector.

import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { usePathKeyedFetch } from "./usePathKeyedFetch";

interface LeafCycleIndex {
  datasetName: string | null;
  createdAt: string | null;
}

const EMPTY: LeafCycleIndex = { datasetName: null, createdAt: null };

export function useLeafCycleIndex(
  path: CyclePath | null,
  rootDatasetName: string | null,
): LeafCycleIndex {
  // Depth 1 (or no path) — the leaf IS the root; the workspace knows its name and
  // the shell already fetches the root started-at. Null key ⇒ no fetch, no flash.
  const isDeep = !!path && path.length > 1;
  const key = isDeep ? encodeCyclePath(path) : null;

  const { value } = usePathKeyedFetch<LeafCycleIndex>(
    key,
    path,
    EMPTY,
    async (p, signal) => {
      const [idxResp, campResp] = await Promise.all([
        fetchCycleFileByPath(p, "cycle", "index.json", signal),
        fetchCycleFileByPath(p, "campaign", "campaign.json", signal),
      ]);
      const idx = idxResp.content ? JSON.parse(idxResp.content) : {};
      const camp = campResp.content ? JSON.parse(campResp.content) : {};
      const datasetName =
        typeof camp.dataset_name === "string" && camp.dataset_name ? camp.dataset_name : null;
      const createdAt = typeof idx.created_at === "string" ? idx.created_at : null;
      return { datasetName, createdAt };
    },
  );

  // Depth 1: dataset name is the workspace's; `createdAt` stays null so the caller
  // keeps its root-sourced started-at. Deep: the stamped leaf value (empty until
  // the fetch for the CURRENT leaf lands — never the root's for a frame).
  return isDeep ? value : { datasetName: rootDatasetName, createdAt: null };
}
