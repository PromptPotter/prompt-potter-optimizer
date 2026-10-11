"use client";

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
