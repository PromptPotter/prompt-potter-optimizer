import type { DraftCampaignWire } from "@/lib/api";

// A draft belongs to a CAMPAIGN, and this is the only place that says so.
//
// `draft_id` IS the check-in campaign's id — `create_checkin_campaign` re-keys the draft to it at
// the mint, so the two are one object under two names from that moment on. The ingest thread that
// holds the draft is APP-WIDE though: it survives selecting a different campaign in the sidebar.
// Nothing compared the two ids, so a draft opened in "New campaign" rendered over whichever
// campaign the operator clicked next — reach `ready`, select a running campaign, and its node
// panel showed the draft's searchpoint instead of the run's.
//
// The narrow shape is deliberate: it takes the id and the draft, not the ingest hook, so the rule
// is testable without a React tree and cannot quietly grow a second input.
export function draftForCampaign(
  draft: DraftCampaignWire | null | undefined,
  campaignId: string | null | undefined,
): DraftCampaignWire | null {
  if (!draft || !campaignId) return null;
  return draft.draft_id === campaignId ? draft : null;
}
