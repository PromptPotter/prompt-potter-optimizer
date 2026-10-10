"use client";
// Only a drill-in reads the pipeline: an inner cycle's `.inner/` sandbox is absent from `/cycles`.

import type { CyclePath } from "../ids";
import { useViewedDatasetName } from "../workspace";
import { useCampaignPipeline } from "./useConnector";
import { readyData } from "./useRead";

export function useLeafDatasetName(
  path: CyclePath | null,
  leafCampaignId: string | null,
  at: string | null,
): string | null {
  const rootDatasetName = useViewedDatasetName();
  const deep = path !== null && path.length > 1;
  const served = readyData(useCampaignPipeline(deep ? leafCampaignId : null, at));
  if (!deep) return rootDatasetName;
  return served === null ? null : served.dataset_name;
}
