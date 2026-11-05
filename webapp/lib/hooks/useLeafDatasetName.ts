// Only a drill-in reads the pipeline: an inner cycle's `.inner/` sandbox is absent from `/hierarchy`.

import { useMemo } from "react";
import { subjectKey } from "../api/reads";
import { pathLeaf } from "../ids";
import { useViewedDatasetName, useWorkspace } from "../workspace";
import { useCampaignPipeline } from "./useConnector";
import { readyData } from "./useRead";

// The viewed leaf as a course subject: the hops ABOVE it are its chain, so an L4 inner run
// resolves its OWN pipeline.
export function useLeafSubject(): string | null {
  const { viewedPath } = useWorkspace();
  return useMemo(() => {
    const leaf = viewedPath ? pathLeaf(viewedPath) : null;
    return leaf && viewedPath
      ? subjectKey("course", [leaf.campaignId, leaf.cycleId], viewedPath.slice(0, -1))
      : null;
  }, [viewedPath]);
}

export function useLeafDatasetName(): string | null {
  const { viewedPath, leafCampaignId } = useWorkspace();
  const rootDatasetName = useViewedDatasetName();
  const deep = viewedPath !== null && viewedPath.length > 1;
  const served = readyData(useCampaignPipeline(deep ? leafCampaignId : null, useLeafSubject()));
  if (!deep) return rootDatasetName;
  return served === null ? null : served.dataset_name;
}
