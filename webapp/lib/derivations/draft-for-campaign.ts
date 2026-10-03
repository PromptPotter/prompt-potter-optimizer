import type { DraftCampaignWire } from "@/lib/api";

// `draft_id` IS the check-in campaign's id, but the ingest thread holding the draft is app-wide —
// so a draft must never render over another selected campaign.
export function draftForCampaign(
  draft: DraftCampaignWire | null | undefined,
  campaignId: string | null | undefined,
): DraftCampaignWire | null {
  if (!draft || !campaignId) return null;
  return draft.draft_id === campaignId ? draft : null;
}
