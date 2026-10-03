"use client";
// The disabled footer search icon is an INTENTIONAL placeholder — out of scope for any "hide
// non-functional controls" sweep. Peers: `chat/ChatPane.tsx`, `account/AccountModal.tsx`.
import { useEffect, useMemo, useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { BRAND } from "@/lib/brand";
import { TERMS } from "@/lib/terms";
import { Term } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useNodeToggle } from "@/lib/view-memory";
import { useShowCandidates } from "@/lib/tree-prefs";
import { applyTheme, readStoredTheme } from "@/lib/theme";
import { buildForest, nodeKey } from "@/lib/derivations";
import type { TreeCtx } from "./sidebar/ForestRows";
import { AccountSpend } from "./sidebar/AccountSpend";
import { SidebarContent } from "./SidebarContent";

interface Props {
  onSelectPath: (path: CyclePath, candidate?: string | null) => void;
  onNewCycle: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

// A FOREST, recursive: Forest → Origin → Run → Cycle-tree → (Inner Forest). An origin tier
// renders only when it groups 2+ runs; a single-run origin IS its run row. An origin's identity is
// its spec's content hash (the root `cycle_<hash>`), and `.inner/<cycle_id>` is just another store.

function flipTheme() {
  applyTheme(readStoredTheme() === "light" ? "dark" : "light");
}

export function Sidebar({
  onSelectPath,
  onNewCycle,
  collapsed,
  onToggleCollapse,
}: Props) {
  const {
    viewedPath,
    viewedCandidateId,
    campaigns,
    cycles,
    cyclesLoaded,
    campaignsLoaded,
    activeCycleId,
    activeCampaignId,
    lifecycleFilter,
    setLifecycleFilter,
    openAccount,
  } = useWorkspace();
  const nodes = useNodeToggle();
  const [showCandidates] = useShowCandidates();
  // null = all datasets.
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  // Authed and anon footer control sets are mutually exclusive (frontend-surface-contract § I4).
  const { status, openAuthPrompt } = useAuth();
  // Nothing polls the session; the navigation below is the read-back.
  const logout = useCommand<"logout">("sidebar-session", { revalidate: false });
  const handleSignOut = () =>
    void logout.run("logout", postLogout, () => {
      window.location.href = "/login/";
    });

  // Filter BEFORE grouping: an origin's run count decides whether its tier renders at all.
  const origins = useMemo(() => {
    const kept =
      datasetFilter == null
        ? campaigns
        : campaigns.filter(
            (c) => (c.dataset_name || "(unknown)") === datasetFilter,
          );
    return buildForest(kept, cycles);
  }, [campaigns, cycles, datasetFilter]);

  const datasetNames = useMemo(() => {
    const s = new Set<string>();
    for (const c of campaigns) s.add(c.dataset_name || "(unknown)");
    return [...s].sort();
  }, [campaigns]);

  // Keyed on the ACTIVE cycle only, never the viewed one: a manual selection must not pop a
  // course open.
  const focusKey = useMemo(() => {
    if (!activeCampaignId || !activeCycleId) return null;
    const path = encodeCyclePath([
      { campaignId: activeCampaignId, cycleId: rootCycleId(activeCycleId) },
    ]);
    return nodeKey("course", path);
  }, [activeCampaignId, activeCycleId]);

  // ONCE per (campaign, active cycle): unlatched, every remount re-opens the row the operator
  // just collapsed.
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
      selectCyclePath: onSelectPath,
      showCandidates,
    }),
    [nodes, viewedPath, viewedCandidateId, onSelectPath, showCandidates],
  );

  // Both lists, so switching lifecycle tab shows `loading…` rather than the prior tab's rows.
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
        <div className="brand-lockup">
          <div className="brand-mark">
            <PotterMark size={12} />
          </div>
          <span className="brand-name">PromptPotter</span>
        </div>
        <div className="brand-sub">
          <Term content={TERMS.brand_live_preview}>LIVE PREVIEW</Term>
        </div>
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
        origins={origins}
        ctx={ctx}
      />
      <AccountSpend />
      <div className="sidebar-footer">
        <div className="sidebar-footer-chrome">
          <button
            type="button"
            className="sidebar-search"
            aria-label="Search analytics (coming soon)"
            title="Search analytics"
            disabled
          >
            <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <circle cx="8" cy="8" r="5" />
              <line x1="12" y1="12" x2="15" y2="15" />
            </svg>
          </button>
          <button
            className="theme-toggle"
            type="button"
            onClick={flipTheme}
            title="Toggle bright / dark theme"
            aria-label="Toggle theme"
          >
            <svg className="sun" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
              <circle cx="8" cy="8" r="3" />
              <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
            </svg>
            <svg className="moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M6 1.5A6.5 6.5 0 1 0 14.5 10 5 5 0 0 1 6 1.5z" />
            </svg>
          </button>
          {status === "authed" ? (
            <button
              type="button"
              className="account-trigger"
              aria-label="Open account"
              onClick={() => openAccount()}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
                <circle cx="8" cy="5.5" r="2.5" />
                <path d="M2.5 14c.8-2.5 3-4 5.5-4s4.7 1.5 5.5 4" />
              </svg>
            </button>
          ) : null}
        </div>
        {status === "unauthed" && (
          <div className="sidebar-footer-auth">
            <button
              type="button"
              className="auth-chip auth-chip-gold"
              onClick={openAuthPrompt}
            >
              Log in
            </button>
            <button
              type="button"
              className="auth-chip auth-chip-rust"
              onClick={openAuthPrompt}
            >
              Sign up for free
            </button>
          </div>
        )}
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
            disabled={logout.pending !== null}
          >
            {logout.pending !== null ? "Signing out…" : "Log out"}
          </button>
        )}
      </div>
    </nav>
  );
}
