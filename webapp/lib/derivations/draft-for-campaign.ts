import type { DraftCampaignWire } from "@/lib/api";

// The ingest thread holding the draft is app-wide, so a draft must not render over another campaign.
export function draftForCampaign(
  draft: DraftCampaignWire | null | undefined,
  campaignId: string | null | undefined,
): DraftCampaignWire | null {
  if (!draft || !campaignId) return null;
  return draft.draft_id === campaignId ? draft : null;
}
