"use client";
import { useEffect, useMemo, useState } from "react";
import { SignInPrompt } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { filterForest, nodeKey } from "@/lib/derivations";
import { encodeCyclePath, rootCycleId } from "@/lib/ids";
import { useActivePointer, useRegistry } from "@/lib/registry";
import { useShowCandidates } from "@/lib/tree-prefs";
import { useNodeToggle } from "@/lib/view-memory";
import { useWorkspace } from "@/lib/workspace";
import { ForestRows, type TreeCtx } from "./ForestRows";
import { SidebarFilterPopover } from "./SidebarFilterPopover";

export function SidebarContent() {
  const { status } = useAuth();
  const { viewedPath, viewedCandidateId } = useWorkspace();
  const {
    campaigns,
    forest,
    cyclesLoaded,
    campaignsLoaded,
    failure,
    lifecycleFilter,
    setLifecycleFilter,
  } = useRegistry();
  const { campaignId: activeCampaignId, cycleId: activeCycleId } = useActivePointer();
  const nodes = useNodeToggle();
  const [showCandidates] = useShowCandidates();
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  const origins = useMemo(
    () =>
      datasetFilter == null
        ? forest
        : filterForest(forest, (c) => (c.dataset_name || "(unknown)") === datasetFilter),
    [forest, datasetFilter],
  );

  const datasetNames = useMemo(() => {
    const s = new Set<string>();
    for (const c of campaigns) s.add(c.dataset_name || "(unknown)");
    return [...s].sort();
  }, [campaigns]);

  // The ACTIVE cycle, never the viewed one: a manual selection must not pop a course open.
  const focusKey = useMemo(() => {
    if (!activeCampaignId || !activeCycleId) return null;
    const path = encodeCyclePath([
      { campaignId: activeCampaignId, cycleId: rootCycleId(activeCycleId) },
    ]);
    return nodeKey("course", path);
  }, [activeCampaignId, activeCycleId]);

  // Latched per (campaign, active cycle): unlatched, a remount re-opens a row just collapsed.
  useEffect(() => {
    if (!focusKey || !activeCampaignId || !activeCycleId) return;
    if (nodes.autoExpandedFor(activeCampaignId) === activeCycleId) return;
    nodes.markAutoExpanded(activeCampaignId, activeCycleId, focusKey);
  }, [focusKey, activeCampaignId, activeCycleId, nodes]);

  const ctx: TreeCtx = useMemo(
    () => ({
      isNodeOpen: nodes.isOpen,
      toggleNode: nodes.toggle,
      viewedPath,
      viewedCandidateId,
      showCandidates,
    }),
    [nodes, viewedPath, viewedCandidateId, showCandidates],
  );

  const loaded = cyclesLoaded && campaignsLoaded;
  const error = failure?.message ?? null;
  const filtered = lifecycleFilter === "archived" || datasetFilter != null;
  const clearFilters = () => {
    setLifecycleFilter("active");
    setDatasetFilter(null);
  };

  return (
    <div className="unit-library">
      <div className="unit-library-head">
        <span>Campaigns</span>
        <SidebarFilterPopover
          lifecycleFilter={lifecycleFilter}
          setLifecycleFilter={setLifecycleFilter}
          datasetNames={datasetNames}
          datasetFilter={datasetFilter}
          setDatasetFilter={setDatasetFilter}
        />
      </div>
      {filtered && (
        <div className="unit-library-active-filter">
          <span className="unit-library-active-filter-text">
            {lifecycleFilter === "archived" && <span>Archived</span>}
            {datasetFilter != null && <span>{datasetFilter}</span>}
          </span>
          <button
            type="button"
            className="unit-library-active-filter-clear"
            onClick={clearFilters}
            title="Clear filters"
            aria-label="Clear filters"
          >
            ✕
          </button>
        </div>
      )}
      {status !== "authed" ? (
        status === "loading" ? (
          <div className="unit-library-note">loading…</div>
        ) : (
          <SignInPrompt
            className="unit-library-note"
            message="Sign in to see your campaigns."
          />
        )
      ) : (
        !loaded && (
          <div className="unit-library-note">{error ?? "loading…"}</div>
        )
      )}
      {loaded && origins.length === 0 && lifecycleFilter === "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No archived campaigns</div>
          <div className="empty-body">
            Archive a campaign from its <code>⋯</code> menu to declutter the
            active list. Archives are reversible from this tab.
          </div>
        </div>
      )}
      {loaded && origins.length === 0 && lifecycleFilter !== "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No campaigns yet</div>
          <div className="empty-body">
            Hit <strong>+ New campaign</strong> above. Bring the data your pipeline gets
            wrong — the Potter reads it, sets up the first run, and starts improving.
          </div>
        </div>
      )}
      {loaded && origins.length > 0 && (
        <ul className="unit-library-list">
          <ForestRows origins={origins} ctx={ctx} />
        </ul>
      )}
    </div>
  );
}
