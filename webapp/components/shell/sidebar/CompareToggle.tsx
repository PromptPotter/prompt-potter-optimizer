"use client";

import { defaultChannel, useCompareSelection } from "@/lib/compare-selection";

export function CompareToggle({
  campaignId,
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
        e.stopPropagation();
        toggleCampaign(campaignId, defaultChannel(campaignId, answeringCycleId).subject);
      }}
    >
      {on ? "◧" : "▢"}
    </button>
  );
}
