"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { TERMS } from "@/lib/terms";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useNodeToggle } from "@/lib/view-memory";
import { buildForest, nodeKey } from "./sidebar/grouping";
import type { TreeCtx } from "./sidebar/ForestRows";
import { SidebarContent } from "./SidebarContent";

interface Props {
  // Selection carries the whole CyclePath — a cycle_id is ambiguous across
  // campaigns, and a campaign+cycle pair is ambiguous across stores once an
  // inner forest is open. The path is the one unambiguous address at any depth.
  onSelectPath: (path: CyclePath, candidate?: string | null) => void;
  onNewCycle: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

// The sidebar is a FOREST, and the shape repeats at every depth:
//
//   Forest → Origin → Run → Cycle-tree → (Inner Forest)
//
// An **origin** is the complete specification the loop starts from; its identity
// is the content hash of that spec, which is exactly the root cycle id
// (`cycle_<hash>`). A **run** is one campaign (`{dataset}__{rand6}`) measuring it
// — so two campaigns on an unchanged declaration are two runs of ONE origin, and
// the origin tier groups them. Each run is itself a tree: a root cycle + its
// forks / diags / sweeps. Any cycle can open its own **inner forest** (an L4
// `promptpotter-self` fan-out lives in a `.inner/<cycle_id>` sandbox, which is
// structurally just another store) — that closes the recursion, so L5+ needs no
// new tier.
//
// This tier renders origins in one recency-sorted list; the header's filter
// popover (lifecycle + dataset) narrows it. The single-run origin — the common
// case — collapses: the run row IS that origin and opens it directly. The origin
// tier appears only when it actually groups 2+ runs, i.e. when it carries
// information.
//
// Only the tenant's own store is polled here; a course's subtree fetches on expand
// via `useLineageTree` (one keyed store — a collapsed row subscribes to nothing).

export function Sidebar({ onSelectPath, onNewCycle, collapsed, onToggleCollapse }: Props) {
  // Cycle list + campaign registry + active pointer + current selection all
  // come from the shared workspace context — one poll for the whole app.
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
  } = useWorkspace();
  // Expand/collapse, remembered PER CAMPAIGN (`lib/view-memory.tsx`) — the campaign a node
  // belongs to is read off its own address, so nothing here has to carry one.
  const nodes = useNodeToggle();
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

  // Filter BEFORE grouping: the dataset filter drops runs, and an origin's run
  // count is what decides whether its tier renders at all. Grouping first would
  // leave an origin claiming "2 runs" while showing one.
  const origins = useMemo(() => {
    const kept =
      datasetFilter == null
        ? campaigns
        : campaigns.filter(
            (c) => (c.dataset_name || "(unknown)") === datasetFilter,
          );
    return buildForest(kept, cycles);
  }, [campaigns, cycles, datasetFilter]);

  // Distinct dataset names, for the filter popover's dataset picker.
  const datasetNames = useMemo(() => {
    const s = new Set<string>();
    for (const c of campaigns) s.add(c.dataset_name || "(unknown)");
    return [...s].sort();
  }, [campaigns]);

  // Auto-expand the ACTIVE run's root course so the live loop reveals itself without a
  // click — "where's my run?". Keyed on the ACTIVE cycle ONLY, never the viewed one:
  // clicking a row selects it (and moves the viewed path), but expanding is the triangle's
  // job, so a manual selection must not pop a course open. We never auto-collapse; explicit
  // collapse beats helpfulness. Targets the campaign's ROOT course, the node a fork hides under.
  const focusKey = useMemo(() => {
    if (!activeCampaignId || !activeCycleId) return null;
    const path = encodeCyclePath([
      { campaignId: activeCampaignId, cycleId: rootCycleId(activeCycleId) },
    ]);
    return nodeKey("course", path);
  }, [activeCampaignId, activeCycleId]);

  // The stored set is every node TOGGLED AWAY FROM ITS DEFAULT, and a course defaults
  // CLOSED (opening one fetches its lineage) — so revealing means ADDING the key.
  //
  // ONCE per (campaign, active cycle), latched on `autoExpandedFor`. Unlatched, this fires
  // again on every sidebar remount and re-opens the very row the operator just collapsed —
  // "we never auto-collapse; explicit collapse beats helpfulness" was the intent, but a
  // reveal that keeps re-firing overrides an explicit collapse just as surely.
  useEffect(() => {
    if (!focusKey || !activeCampaignId || !activeCycleId) return;
    if (nodes.autoExpandedFor(activeCampaignId) === activeCycleId) return;
    nodes.markAutoExpanded(activeCampaignId, activeCycleId, focusKey);
  }, [focusKey, activeCampaignId, activeCycleId, nodes]);

  // Everything the tree needs, at any depth. The pointer here is the TOP-LEVEL
  // store's; each inner forest overrides it with its own (a sandbox's live loop
  // is not the workspace's active session).
  const ctx: TreeCtx = useMemo(
    () => ({
      isNodeOpen: nodes.isOpen,
      toggleNode: nodes.toggle,
      viewedPath,
      viewedCandidateId,
      selectCyclePath: onSelectPath,
      activeCampaignId,
      activeCycleId,
    }),
    [nodes, viewedPath, viewedCandidateId, onSelectPath, activeCampaignId, activeCycleId],
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
        <div className="brand-lockup">
          <div className="brand-mark">
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
        origins={origins}
        ctx={ctx}
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
