"use client";
// A campaign being set up previews the DRAFT's searchpoint, only for the campaign that draft is:
// the ingest thread outlives a sidebar selection. Read by whichever surface opens a node, so a
// check-in's node shows what is being authored rather than a resolution that has not run.

import { useMemo } from "react";
import { draftForCampaign } from "@/lib/derivations";
import { useIngest } from "@/lib/ingest-flow";
import { useWorkspace } from "@/lib/workspace";

export function useDraftAuthoring() {
  const { flow } = useIngest();
  const { leafCampaignId } = useWorkspace();
  const previewDraft = draftForCampaign(
    flow.phase.stage === "ready" || flow.phase.stage === "awaiting-context"
      ? flow.phase.draft
      : null,
    leafCampaignId,
  );
  // The draft's documents, not the wire, so config is read only where the served resolution answered.
  return useMemo(
    () =>
      previewDraft
        ? {
            overlay: previewDraft.pipeline_overlay,
            promptFields: previewDraft.origin_prompt_fields,
          }
        : undefined,
    [previewDraft],
  );
}
