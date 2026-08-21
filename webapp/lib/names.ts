// Campaign + session display names — the single rename seam.
//
// Every surface that shows a human-readable campaign or unit name resolves it
// here, so adding an operator rename feature is a one-field change:
//   - campaign rename writes `CampaignSummary.label` (already wired end-to-end) —
//     `campaignDisplayName` already prefers it;
//   - session rename will add an optional per-session label override consumed
//     by `unitDisplayName`. No session-label field exists yet.

import type { CampaignSummary, CycleListEntry, MintKind } from "./api";
import { shortFamilyTail } from "./ids";

// How the operator reads what minted a cycle. A campaign has exactly one root,
// which reads as "Session"; the others tag a fork / diag / sweep branch.
const MINT_KIND_LABEL: Record<MintKind, string> = {
  session: "Session",
  divergent_resume: "divergent resume",
  user_fork: "user fork",
  auto_rebase: "auto rebase",
};

// Campaign row name — the operator label when set, else the dataset name
// (the campaign IS "the {dataset} experiment"), else the raw id.
export function campaignDisplayName(c: CampaignSummary): string {
  return c.label || c.dataset_name || c.campaign_id;
}

// Human name for one unit — "Session" for the campaign's root, "{kind} {tail}"
// for a fork / diag / sweep branch.
export function unitDisplayName(c: CycleListEntry): string {
  if (c.is_root) return MINT_KIND_LABEL.session;
  return `${MINT_KIND_LABEL[c.mint_kind]} ${shortFamilyTail(c.cycle_id)}`;
}
