"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { rootCycleId } from "@/lib/ids";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { TERMS } from "@/lib/terms";
import {
  COLLAPSED_STORAGE_KEY,
  EMPTY_COLLAPSED,
  collapsedCodec,
  groupCampaigns,
  sessKey,
} from "./sidebar/grouping";
import { SidebarContent } from "./SidebarContent";

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
// one flat, recency-sorted list; the header's filter popover
// (lifecycle + dataset) narrows it. The single-session campaign — the common case —
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
    campaignsLoaded,
    activeCycleId,
    activeCampaignId,
    lifecycleFilter,
    setLifecycleFilter,
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

  // Auth drives the footer (Log out is authed-only — anon sees the Topbar's
  // Log in / Sign up instead; frontend-surface-contract.md § I4) and the
  // campaign-list resting state (anon → sign-in prompt, not perpetual loading).
  const { status } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const handleSignOut = useCallback(async () => {
    setSigningOut(true);
    try {
      await postLogout();
      window.location.href = "/login/";
    } catch {
      setSigningOut(false);
    }
  }, []);

  const allGroups = useMemo(
    () => groupCampaigns(campaigns, cycles),
    [campaigns, cycles],
  );

  // Distinct dataset names, for the filter popover's dataset picker.
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

  // Wait for BOTH the cycle list and the campaign list for the CURRENT
  // lifecycle tab — so switching to Archived shows `loading…`, not the
  // Active tab's stale rows, until the archived list arrives.
  const loaded = cyclesLoaded && campaignsLoaded;

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
      <SidebarContent
        status={status}
        loaded={loaded}
        lifecycleFilter={lifecycleFilter}
        setLifecycleFilter={setLifecycleFilter}
        datasetNames={datasetNames}
        datasetFilter={datasetFilter}
        setDatasetFilter={setDatasetFilter}
        groups={groups}
        collapsedNodes={collapsedNodes}
        toggleNode={toggleNode}
        campaignId={campaignId}
        cycleId={cycleId}
        activeCampaignId={activeCampaignId}
        activeCycleId={activeCycleId}
        onSelectCycle={onSelectCycle}
      />
      <div className="sidebar-footer">
        <a
          className="sidebar-footer-item"
          href={BRAND.supportUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Support
        </a>
        {status === "authed" && (
          <button
            type="button"
            className="sidebar-footer-item"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            {signingOut ? "Signing out…" : "Log out"}
          </button>
        )}
      </div>
    </nav>
  );
}
