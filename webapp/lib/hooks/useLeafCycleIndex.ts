"use client";
// The VIEWED LEAF hop's dataset name + started-at. Only a drill-in fetches: an inner cycle's
// `.inner/` sandbox is absent from `/cycles`, which answers the root.

import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { readyData, useRead } from "./useRead";

interface LeafCycleIndex {
  datasetName: string | null;
  createdAt: string | null;
}

const EMPTY: LeafCycleIndex = { datasetName: null, createdAt: null };

export function useLeafCycleIndex(
  path: CyclePath | null,
  rootDatasetName: string | null,
): LeafCycleIndex {
  const isDeep = !!path && path.length > 1;

  const read = useRead(
    isDeep && path
      ? {
          key: encodeCyclePath(path),
          fetch: async (signal): Promise<LeafCycleIndex> => {
            const [idxResp, campResp] = await Promise.all([
              fetchCycleFileByPath(path, "cycle", "index.json", signal),
              fetchCycleFileByPath(path, "campaign", "campaign.json", signal),
            ]);
            const idx = idxResp.content ? JSON.parse(idxResp.content) : {};
            const camp = campResp.content ? JSON.parse(campResp.content) : {};
            const datasetName =
              typeof camp.dataset_name === "string" && camp.dataset_name
                ? camp.dataset_name
                : null;
            const createdAt = typeof idx.created_at === "string" ? idx.created_at : null;
            return { datasetName, createdAt };
          },
        }
      : null,
    { surface: "leaf-index" },
  );

  // Depth 1: `createdAt` null means the caller keeps its root-sourced started-at.
  return isDeep ? (readyData(read) ?? EMPTY) : { datasetName: rootDatasetName, createdAt: null };
}
