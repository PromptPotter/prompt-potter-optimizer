import type { CycleListEntry } from "./api";
import { MINT_KIND_LABELS } from "./api/types.generated";
import { shortFamilyTail } from "./ids";

// A branch only — a campaign's name is the served `CampaignSummary.display_name`.
export function unitDisplayName(c: CycleListEntry): string {
  return `${MINT_KIND_LABELS[c.mint_kind]} ${shortFamilyTail(c.cycle_id)}`;
}
