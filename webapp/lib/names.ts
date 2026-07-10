// Campaign + session display names — the single rename seam.
//
// Every surface that shows a human-readable campaign or unit name resolves it
// here, so adding an operator rename feature is a one-field change:
//   - campaign rename writes `CampaignSummary.label` (already wired end-to-end) —
//     `campaignDisplayName` already prefers it;
//   - session rename will add an optional per-session label override consumed
//     by `unitDisplayName`. No session-label field exists yet.

import type { CampaignSummary, CycleListEntry, UnitKind } from "./api";
import { shortFamilyTail } from "./ids";

// Operator-facing unit-kind labels — the time-horizon taxonomy. A campaign
// has exactly one root, which reads as "Session"; the others tag a fork /
// diag / sweep branch.
export const UNIT_KIND_LABEL: Record<UnitKind, string> = {
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
  if (c.is_root) return UNIT_KIND_LABEL.session;
  return `${UNIT_KIND_LABEL[c.unit_kind]} ${shortFamilyTail(c.cycle_id)}`;
}
