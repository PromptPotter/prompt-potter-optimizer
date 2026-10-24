"use client";

import type { HierarchyCampaign } from "@/lib/api";
import { defaultChannel, useCompareSelection } from "@/lib/compare-selection";

export function CompareToggle({ campaign }: { campaign: HierarchyCampaign }) {
  const { hasCampaign, toggleCampaign } = useCompareSelection();
  const on = hasCampaign(campaign.address);
  return (
    <button
      type="button"
      className="unit-library-compare"
      aria-pressed={on}
      title={on ? "On the Compare board — click to take it off" : "Compare this campaign"}
      aria-label={on ? "Remove from the comparison" : "Add to the comparison"}
      onClick={(e) => {
        e.stopPropagation();
        const channel = defaultChannel(campaign);
        if (channel) toggleCampaign(channel.rootCampaignId, channel.subject);
      }}
    >
      {on ? "◧" : "▢"}
    </button>
  );
}
