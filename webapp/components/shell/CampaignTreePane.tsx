"use client";
import { CampaignNode } from "./sidebar/CampaignNode";
import type { CampaignGroup } from "./sidebar/grouping";

interface Props {
  groups: CampaignGroup[];
  collapsedNodes: Set<string>;
  toggleNode: (key: string) => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

// The campaign forest list — one CampaignNode per (filtered) group. Pure
// renderer extracted from Sidebar; the shell owns the workspace data,
// collapse state, and dataset filter that produce `groups`.
export function CampaignTreePane({
  groups,
  collapsedNodes,
  toggleNode,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: Props) {
  return (
    <ul className="unit-library-list">
      {groups.map((cg) => (
        <li key={cg.campaign.campaign_id}>
          <CampaignNode
            group={cg}
            collapsedNodes={collapsedNodes}
            toggleNode={toggleNode}
            campaignId={campaignId}
            cycleId={cycleId}
            activeCampaignId={activeCampaignId}
            activeCycleId={activeCycleId}
            onSelectCycle={onSelectCycle}
          />
        </li>
      ))}
    </ul>
  );
}
