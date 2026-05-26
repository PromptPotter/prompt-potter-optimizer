"use client";
import { campaignOriginHash } from "@/lib/ids";
import { campaignDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { sessKey, type CampaignGroup } from "./grouping";
import { SessionSubtree } from "./SessionSubtree";
import { CampaignMenu } from "./CampaignMenu";

// One campaign in the flat list. A single-session campaign collapses: the
// campaign row IS that session and opens it directly (its twist, if any,
// expands the session's fork-tree). A multi-session campaign expands into
// a session row per session.
export function CampaignNode({
  group,
  collapsedNodes,
  toggleNode,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  group: CampaignGroup;
  collapsedNodes: Set<string>;
  toggleNode: (key: string) => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  const cid = group.campaign.campaign_id;
  const cmpKey = `cmp:${cid}`;
  const cmpOpen = !collapsedNodes.has(cmpKey);
  const single = group.sessions.length === 1;

  // Single-session campaign — the campaign row IS the session.
  if (single) {
    const session = group.sessions[0];
    return (
      <SessionSubtree
        campaign={group.campaign}
        session={session}
        isCampaignRow
        open={cmpOpen}
        onToggle={() => toggleNode(cmpKey)}
        campaignId={campaignId}
        cycleId={cycleId}
        activeCampaignId={activeCampaignId}
        activeCycleId={activeCycleId}
        onSelectCycle={onSelectCycle}
      />
    );
  }

  // Multi-session campaign — a grouping row that expands to session rows.
  const containsViewed = cid === campaignId;
  const containsActive = cid === activeCampaignId;
  const best = group.sessions.reduce<number | null>((m, s) => {
    const a = s.root.best_accuracy;
    return a != null && (m == null || a > m) ? a : m;
  }, null);
  return (
    <>
      <div className={`unit-library-family${containsViewed ? " selected" : ""}`}>
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => toggleNode(cmpKey)}
          aria-label={cmpOpen ? "Collapse sessions" : "Expand sessions"}
          aria-expanded={cmpOpen}
          tabIndex={-1}
        >
          {cmpOpen ? "▼" : "▶"}
        </button>
        <button
          type="button"
          className="unit-library-item"
          onClick={() => toggleNode(cmpKey)}
          title={cid}
        >
          <span className="unit-library-mark">{containsActive ? "●" : ""}</span>
          <span className="unit-library-row">
            <span className="unit-library-name">
              {campaignDisplayName(group.campaign)}
              {!group.campaign.label && (
                <span className="unit-library-hash" title={cid}>
                  #{campaignOriginHash(cid).slice(0, 6)}
                </span>
              )}
            </span>
            <span className="unit-library-meta">
              {group.sessions.length} sessions · {fmtPct0(best)}
            </span>
          </span>
        </button>
        <CampaignMenu campaign={group.campaign} />
      </div>
      {cmpOpen && (
        <ul className="unit-library-children">
          {group.sessions.map((session) => {
            const sKey = sessKey(cid, session.root.cycle_id);
            return (
              <li key={session.root.cycle_id}>
                <SessionSubtree
                  campaign={group.campaign}
                  session={session}
                  isCampaignRow={false}
                  open={!collapsedNodes.has(sKey)}
                  onToggle={() => toggleNode(sKey)}
                  campaignId={campaignId}
                  cycleId={cycleId}
                  activeCampaignId={activeCampaignId}
                  activeCycleId={activeCycleId}
                  onSelectCycle={onSelectCycle}
                />
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
