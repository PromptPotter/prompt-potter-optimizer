// Only a drill-in reads the pipeline: an inner cycle's `.inner/` sandbox is absent from `/hierarchy`.

import { useViewedDatasetName, useWorkspace } from "@/lib/state/workspace";
import { useCampaignPipeline, useLeafSubject } from "./useConnector";
import { readyData } from "./useRead";

export function useLeafDatasetName(): string | null {
  const { viewed } = useWorkspace();
  const rootDatasetName = useViewedDatasetName();
  const deep = viewed !== null && viewed.path.length > 1;
  const served = readyData(useCampaignPipeline(deep ? viewed.leaf.campaignId : null, useLeafSubject()));
  if (!deep) return rootDatasetName;
  return served === null ? null : served.dataset_name;
}
