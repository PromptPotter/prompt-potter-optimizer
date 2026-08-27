"use client";
// Put this campaign on the Compare board, or take it off — on its own sidebar row.
//
// It replaces a "Campaigns ▾" menu that lived in a toolbar on the Compare tab, which was a second
// list of the campaigns already listed two inches to its left. One library, one place to tick.
// The tab then needs no picker row at all, and the same tick works from any tab, because the
// selection is shell-level (`lib/compare-selection.tsx`) rather than the pane's.
//
// It picks the CAMPAIGN, never the point inside it: ticking one lands a channel on that campaign's
// answering branch, read at the winner its last election crowned. Moving it somewhere else is the
// channel card's own cladogram, which is also the thing that can show you where you are.

import { defaultChannel, useCompareSelection } from "@/lib/compare-selection";

export function CompareToggle({
  campaignId,
  // The branch that ANSWERS for this campaign — often a fork rather than the root's own course,
  // which is why the row hands it over instead of this deriving one.
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
        // The row underneath navigates. Ticking a campaign for comparison is a different act and
        // must not also move the dashboard onto it.
        e.stopPropagation();
        toggleCampaign(campaignId, defaultChannel(campaignId, answeringCycleId).subject);
      }}
    >
      {on ? "◧" : "▢"}
    </button>
  );
}
