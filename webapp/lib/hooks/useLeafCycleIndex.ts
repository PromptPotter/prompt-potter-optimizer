"use client";
// The VIEWED LEAF hop's dataset name + started-at. Only a drill-in fetches either: an inner
// cycle's `.inner/` sandbox is absent from `/cycles`, which answers every other cycle.

import { useState } from "react";
import { fetchCycleFileByPath, type CycleListEntry } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { readyData, useRead } from "./useRead";

interface LeafCycleIndex {
  datasetName: string | null;
  createdAt: string | null;
}

export function useLeafCycleIndex(
  path: CyclePath | null,
  rootDatasetName: string | null,
  cycles: CycleListEntry[],
): LeafCycleIndex {
  const leaf = path && path.length > 0 ? path : null;
  const deep = leaf && leaf.length > 1 ? leaf : null;

  const hop = leaf && !deep ? leaf[0] : null;
  const listed = hop
    ? cycles.find((c) => c.campaign_id === hop.campaignId && c.cycle_id === hop.cycleId)
    : undefined;

  const started = useRead(
    deep
      ? {
          key: encodeCyclePath(deep),
          fetch: async (signal): Promise<string | null> => {
            const r = await fetchCycleFileByPath(deep, "cycle", "index.json", signal);
            const idx = r.content ? JSON.parse(r.content) : {};
            return typeof idx.created_at === "string" ? idx.created_at : null;
          },
        }
      : null,
    { surface: "cycle-index" },
  );
  // Only a landed stamp moves it: the prior one stands across a unit switch and a failed read,
  // or the remote strip's ETA would flash "—".
  const landed = deep ? readyData(started) : listed?.created_at || null;
  const [createdAt, setCreatedAt] = useState<string | null>(null);
  if (landed !== null && landed !== createdAt) setCreatedAt(landed);

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

  return { datasetName: deep ? readyData(named) : rootDatasetName, createdAt };
}
