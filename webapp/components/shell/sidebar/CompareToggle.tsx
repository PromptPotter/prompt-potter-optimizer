"use client";
// Put this campaign on the Compare board, or take it off. Picks the CAMPAIGN (its answering
// branch), never a point inside it; the selection is shell-level (`lib/compare-selection.tsx`).

import { defaultChannel, useCompareSelection } from "@/lib/compare-selection";

export function CompareToggle({
  campaignId,
  // Often a fork rather than the root's own course, so the row hands it over.
  answeringCycleId,
}: {
  campaignId: string;
  answeringCycleId: string;
}) {
  const { hasCampaign, toggleCampaign } = useCompareSelection();
  const on = hasCampaign(campaignId);
  return (
    <button
      type="button"
      className="unit-library-compare"
      aria-pressed={on}
      title={on ? "On the Compare board — click to take it off" : "Compare this campaign"}
      aria-label={on ? "Remove from the comparison" : "Add to the comparison"}
      onClick={(e) => {
        // The row underneath navigates; ticking must not also move the dashboard.
        e.stopPropagation();
        toggleCampaign(campaignId, defaultChannel(campaignId, answeringCycleId).subject);
      }}
    >
      {on ? "◧" : "▢"}
    </button>
  );
}
