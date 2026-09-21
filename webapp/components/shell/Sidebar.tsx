"use client";
// The footer search icon is an INTENTIONAL placeholder previewing the analytics-search surface —
// not scaffolding, and out of scope for any "hide non-functional controls" sweep. Peers:
// `chat/ChatPane.tsx`, `account/AccountModal.tsx`.
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
// forks / diags. Any cycle can open its own **inner forest** (an L4
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

// The theme switch reads the stored value at click time rather than holding state:
// `applyTheme` writes the attribute + localStorage and broadcasts to the canvases.
function flipTheme() {
  applyTheme(readStoredTheme() === "light" ? "dark" : "light");
}

export function Sidebar({
  onSelectPath,
  onNewCycle,
  collapsed,
  onToggleCollapse,
}: Props) {
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
    openAccount,
  } = useWorkspace();
  // Expand/collapse, remembered PER CAMPAIGN (`lib/view-memory.tsx`) — the campaign a node
  // belongs to is read off its own address, so nothing here has to carry one.
  const nodes = useNodeToggle();
  // Per-device, not per campaign — so it does not ride view memory's TTL and LRU.
  const [showCandidates] = useShowCandidates();
  // Dataset filter — null = all datasets. Not persisted; resets per visit.
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  // Auth drives the footer — the two control sets are mutually exclusive
  // (frontend-surface-contract.md § I4): authed gets the account button + Log
  // out, anon gets Log in / Sign up. It also drives the campaign-list resting
  // state (anon → sign-in prompt, not perpetual loading).
  const { status, openAuthPrompt } = useAuth();
  // Nothing polls the session; the navigation below is the read-back.
  const logout = useCommand<"logout">("sidebar-session", { revalidate: false });
  const handleSignOut = () =>
    void logout.run("logout", postLogout, () => {
      window.location.href = "/login/";
    });

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
          {/* Search — the disabled placeholder this file's header refuses to sweep.
              It stays in the DOM and stays honestly inert. */}
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
