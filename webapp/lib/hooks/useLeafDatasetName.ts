// Only a drill-in reads the pipeline: an inner cycle's `.inner/` sandbox is absent from `/hierarchy`.

import { useViewedDatasetName, useWorkspace } from "../workspace";
import { useCampaignPipeline, useLeafSubject } from "./useConnector";
import { readyData } from "./useRead";

export function useLeafDatasetName(): string | null {
  const { viewedPath, leafCampaignId } = useWorkspace();
  const rootDatasetName = useViewedDatasetName();
  const deep = viewedPath !== null && viewedPath.length > 1;
  const served = readyData(useCampaignPipeline(deep ? leafCampaignId : null, useLeafSubject()));
  if (!deep) return rootDatasetName;
  return served === null ? null : served.dataset_name;
}
