"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { rootCycleId } from "@/lib/ids";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { TERMS } from "@/lib/terms";
import {
  COLLAPSED_STORAGE_KEY,
  EMPTY_COLLAPSED,
  collapsedCodec,
  groupCampaigns,
  sessKey,
} from "./sidebar/grouping";
import { CampaignNode } from "./sidebar/CampaignNode";

interface Props {
  // A unit is the pair (campaignId, cycleId) — cycle_id alone is ambiguous
  // across campaigns, so selection always carries both.
  onSelectCycle: (campaignId: string, cycleId: string) => void;
  onNewCycle: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

// The sidebar is a forest. A **campaign** is a declared optimization effort
// (a dataset + pipeline origin + context); its id is `{dataset}__{hash}`,
// stable across re-runs. A campaign holds N **sessions** — one per `python
// -m promptpotter new` on that declaration; a session's identity is its
// root cycle (`cycle_{hash}` for session 1, `cycle_{hash}_s{N}` for the
// Nth). Each session is itself a tree: a root + its forks / diag / sweeps.
//
// So the nesting is: campaign → session → fork-tree. Campaigns render in
// one flat, recency-sorted list (a dataset-filter chip-bar at the top
// narrows it). The single-session campaign — by far the common case —
// collapses: the campaign row IS that session and opens it directly. The
// session tier appears only when a campaign has 2+ sessions.

export function Sidebar({ onSelectCycle, onNewCycle, collapsed, onToggleCollapse }: Props) {
  // Cycle list + campaign registry + active pointer + current selection all
  // come from the shared workspace context — one poll for the whole app.
  const {
    cycleId,
    campaignId,
    campaigns,
    cycles,
    cyclesLoaded,
    activeCycleId,
    activeCampaignId,
  } = useWorkspace();
  // Campaign/session collapse state — nodes expand by default, so we
  // persist the ones the operator explicitly collapsed.
  const [collapsedNodes, setCollapsedNodes] = useLocalStorage<Set<string>>(
    COLLAPSED_STORAGE_KEY,
    EMPTY_COLLAPSED,
    collapsedCodec,
  );
  // Dataset filter — null = all datasets. Not persisted; resets per visit.
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  const allGroups = useMemo(
    () => groupCampaigns(campaigns, cycles),
    [campaigns, cycles],
  );

  // Distinct dataset names, for the filter chip-bar.
  const datasetNames = useMemo(() => {
    const s = new Set<string>();
    for (const g of allGroups) s.add(g.campaign.dataset_name || "(unknown)");
    return [...s].sort();
  }, [allGroups]);

  const groups = useMemo(
    () =>
      datasetFilter == null
        ? allGroups
        : allGroups.filter(
            (g) => (g.campaign.dataset_name || "(unknown)") === datasetFilter,
          ),
    [allGroups, datasetFilter],
  );

  // Auto-expand the campaign + session containing the viewed/active cycle
  // — "where am I?" should be visible without a click. We never
  // auto-collapse; explicit collapse beats helpfulness.
  const focusKeys = useMemo(() => {
    const cmpId = campaignId ?? activeCampaignId;
    const cyId = cycleId ?? activeCycleId;
    if (!cmpId || !cyId) return null;
    return { cmp: `cmp:${cmpId}`, sess: sessKey(cmpId, rootCycleId(cyId)) };
  }, [campaignId, activeCampaignId, cycleId, activeCycleId]);

  useEffect(() => {
    if (!focusKeys) return;
    setCollapsedNodes((prev) => {
      if (!prev.has(focusKeys.cmp) && !prev.has(focusKeys.sess)) return prev;
      const next = new Set(prev);
      next.delete(focusKeys.cmp);
      next.delete(focusKeys.sess);
      return next;
    });
  }, [focusKeys, setCollapsedNodes]);

  const toggleNode = useCallback(
    (key: string) => {
      setCollapsedNodes((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
    },
    [setCollapsedNodes],
  );

  const loaded = cyclesLoaded;

  return (
    <nav className="sidebar" aria-label="Primary">
      <button
        type="button"
        className="sidebar-toggle"
        onClick={onToggleCollapse}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
      >
        {collapsed ? "›" : "‹"}
      </button>
      <div className="brand">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
          <div style={{ width: 22, height: 22, background: "var(--color-accent)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <rect x="1" y="1" width="4" height="4" rx="1" fill="white" />
              <rect x="7" y="1" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="1" y="7" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="7" y="7" width="4" height="4" rx="1" fill="white" opacity=".4" />
            </svg>
          </div>
          <span className="brand-name">PromptPotter</span>
        </div>
        <div className="brand-sub" title={TERMS.brand_live_preview}>LIVE PREVIEW</div>
      </div>
      <div className="sidebar-primary">
        <button
          type="button"
          className="sidebar-cta"
          onClick={onNewCycle}
          title="Start a new campaign"
        >
          + New campaign
        </button>
      </div>
      <div className="unit-library">
        <div className="unit-library-head">
          <span>Campaigns</span>
        </div>
        {datasetNames.length > 1 && (
          <DatasetFilterBar
            datasets={datasetNames}
            selected={datasetFilter}
            onSelect={setDatasetFilter}
          />
        )}
        {!loaded && <div className="unit-library-note">loading…</div>}
        {loaded && groups.length === 0 && (
          <div className="unit-library-empty">
            <div className="empty-headline">No campaigns yet</div>
            <div className="empty-body">
              Start your first campaign from a terminal:
            </div>
            <pre className="empty-cmd"><code>python -m promptpotter new &lt;dataset&gt;</code></pre>
            <div className="empty-hint">
              See <code>docs/manual/</code> for the quickstart.
            </div>
          </div>
        )}
        {loaded && groups.length > 0 && (
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
        )}
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-footer-item">Support</div>
        <div className="sidebar-footer-item">Log out</div>
      </div>
    </nav>
  );
}

// Dataset filter chip-bar — narrows the flat campaign list to one dataset.
function DatasetFilterBar({
  datasets,
  selected,
  onSelect,
}: {
  datasets: string[];
  selected: string | null;
  onSelect: (d: string | null) => void;
}) {
  return (
    <div className="unit-library-filter" role="group" aria-label="Filter by dataset">
      <button
        type="button"
        className={`unit-library-filter-chip${selected == null ? " active" : ""}`}
        onClick={() => onSelect(null)}
      >
        All
      </button>
      {datasets.map((d) => (
        <button
          key={d}
          type="button"
          className={`unit-library-filter-chip${selected === d ? " active" : ""}`}
          onClick={() => onSelect(selected === d ? null : d)}
          title={d}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
