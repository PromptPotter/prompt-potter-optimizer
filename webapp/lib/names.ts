// Campaign + session display names — the single rename seam.

import type { CampaignSummary, CycleListEntry, MintKind } from "./api";
import { shortFamilyTail } from "./ids";

const MINT_KIND_LABEL: Record<MintKind, string> = {
  session: "Session",
  divergent_resume: "divergent resume",
  user_fork: "user fork",
  auto_rebase: "auto rebase",
};

export function campaignDisplayName(c: CampaignSummary): string {
  return c.label || c.dataset_name || c.campaign_id;
}

// A branch only — the root is named by its campaign row.
export function unitDisplayName(c: CycleListEntry): string {
  return `${MINT_KIND_LABEL[c.mint_kind]} ${shortFamilyTail(c.cycle_id)}`;
}
