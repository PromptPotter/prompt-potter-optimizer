"use client";
// The VIEWED LEAF hop's dataset name. Only a drill-in fetches it: an inner cycle's `.inner/`
// sandbox is absent from `/cycles`, and every other leaf is the root's own dataset.

import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { readyData, useRead } from "./useRead";

export function useLeafDatasetName(
  path: CyclePath | null,
  rootDatasetName: string | null,
): string | null {
  const deep = path && path.length > 1 ? path : null;

  const named = useRead(
    deep
      ? {
          key: encodeCyclePath(deep),
          fetch: async (signal): Promise<string | null> => {
            const r = await fetchCycleFileByPath(deep, "campaign", "campaign.json", signal);
            const camp = r.content ? JSON.parse(r.content) : {};
            return typeof camp.dataset_name === "string" && camp.dataset_name
              ? camp.dataset_name
              : null;
          },
        }
      : null,
    { surface: "leaf-campaign" },
  );

  return deep ? readyData(named) : rootDatasetName;
}
