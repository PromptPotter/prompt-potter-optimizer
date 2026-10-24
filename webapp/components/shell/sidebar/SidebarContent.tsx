"use client";
import { useEffect, useMemo, useState } from "react";
import { SignInPrompt } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { tagValue } from "@/lib/hierarchy";
import { useActivePointer, useRegistry } from "@/lib/registry";
import { useShowCandidates } from "@/lib/tree-prefs";
import { useNodeToggle } from "@/lib/view-memory";
import { useWorkspace } from "@/lib/workspace";
import { ForestRows, type TreeCtx } from "./ForestRows";
import { SidebarFilterPopover } from "./SidebarFilterPopover";

export function SidebarContent() {
  const { status } = useAuth();
  const { viewedPath, viewedCandidateId } = useWorkspace();
  const { listed, loaded, failure, listedLifecycle, setListedLifecycle } = useRegistry();
  const { campaignId: activeCampaignId, cycleId: activeCycleId } = useActivePointer();
  const nodes = useNodeToggle();
  const [showCandidates] = useShowCandidates();
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  const shown = useMemo(
    () =>
      datasetFilter == null
        ? listed
        : listed.filter((c) => (tagValue(c, "dataset") || "(unknown)") === datasetFilter),
    [listed, datasetFilter],
  );

  const datasetNames = useMemo(() => {
    const s = new Set<string>();
    for (const c of listed) s.add(tagValue(c, "dataset") || "(unknown)");
    return [...s].sort();
  }, [listed]);

  // The ACTIVE cycle, never the viewed one: a manual selection must not pop a course open.
  const focusKey = activeCampaignId && activeCycleId ? activeCampaignId : null;

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

  const error = failure?.message ?? null;
  const filtered = listedLifecycle === "archived" || datasetFilter != null;
  const clearFilters = () => {
    setListedLifecycle("active");
    setDatasetFilter(null);
  };

  return (
    <div className="unit-library">
      <div className="unit-library-head">
        <span>Campaigns</span>
        <SidebarFilterPopover
          listedLifecycle={listedLifecycle}
          setListedLifecycle={setListedLifecycle}
          datasetNames={datasetNames}
          datasetFilter={datasetFilter}
          setDatasetFilter={setDatasetFilter}
        />
      </div>
      {filtered && (
        <div className="unit-library-active-filter">
          <span className="unit-library-active-filter-text">
            {listedLifecycle === "archived" && <span>Archived</span>}
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
      {loaded && shown.length === 0 && listedLifecycle === "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No archived campaigns</div>
          <div className="empty-body">
            Archive a campaign from its <code>⋯</code> menu to declutter the
            active list. Archives are reversible from this tab.
          </div>
        </div>
      )}
      {loaded && shown.length === 0 && listedLifecycle !== "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No campaigns yet</div>
          <div className="empty-body">
            Hit <strong>+ New campaign</strong> above. Bring the data your pipeline gets
            wrong — the Potter reads it, sets up the first run, and starts improving.
          </div>
        </div>
      )}
      {loaded && shown.length > 0 && (
        <ul className="unit-library-list">
          <ForestRows entries={shown} ctx={ctx} />
        </ul>
      )}
    </div>
  );
}
