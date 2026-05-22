"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCampaigns,
  type Campaign,
  type CycleListEntry,
  type UnitKind,
} from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { rootCycleId, shortFamilyTail, sessionIndexOf, campaignOriginHash } from "@/lib/ids";
import { TERMS } from "@/lib/terms";

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

// One session in a campaign's forest: its root cycle + every fork / diag /
// sweep that descends from it.
interface SessionGroup {
  root: CycleListEntry;
  branches: CycleListEntry[];
  // Most-recent updated_at across the session's units.
  updatedAt: string;
}

// One campaign's row in the tree: the manifest + its N sessions.
interface CampaignGroup {
  campaign: Campaign;
  sessions: SessionGroup[];
  // Most-recent updated_at across every session — sorts campaigns so the
  // one being actively worked on stays at the top.
  updatedAt: string;
}

// Operator-facing badge for a non-session unit. The session root carries
// no badge — it's the trunk, not a branch.
const UNIT_KIND_LABEL: Record<Exclude<UnitKind, "session">, string> = {
  divergent_resume: "divergent resume",
  user_fork: "user fork",
  l3_fork: "L3 fork",
};

// One node in a session's fork-tree: a unit plus the units forked off it.
interface UnitNode {
  unit: CycleListEntry;
  children: UnitNode[];
}

const byUpdatedDesc = (a: CycleListEntry, b: CycleListEntry) =>
  a.updated_at < b.updated_at ? 1 : -1;

// Build a session's fork-tree from `parent_cycle_id`. The root is the
// trunk; every branch nests under its parent. A branch whose parent isn't
// in the session attaches to the root so it can never vanish.
function buildUnitTree(root: CycleListEntry, branches: CycleListEntry[]): UnitNode {
  const rootId = root.cycle_id;
  const childrenOf = new Map<string, CycleListEntry[]>();
  for (const u of branches) {
    const parent =
      u.parent_cycle_id &&
      (u.parent_cycle_id === rootId ||
        branches.some((x) => x.cycle_id === u.parent_cycle_id))
        ? u.parent_cycle_id
        : rootId;
    const arr = childrenOf.get(parent) ?? [];
    arr.push(u);
    childrenOf.set(parent, arr);
  }
  const visit = (unit: CycleListEntry): UnitNode => ({
    unit,
    children: (childrenOf.get(unit.cycle_id) ?? [])
      .slice()
      .sort(byUpdatedDesc)
      .map(visit),
  });
  return visit(root);
}

// Build the flat campaign list. Campaigns are the real manifests from
// `GET /campaigns`; cycles come from `/cycles`, partitioned per campaign
// into sessions by their family root (`rootCycleId`). A cycle whose
// campaign isn't in the campaign list is dropped — the registry is the
// source of truth for what's a campaign.
function groupCampaigns(
  campaigns: Campaign[],
  cycles: CycleListEntry[],
): CampaignGroup[] {
  const cyclesByCampaign = new Map<string, CycleListEntry[]>();
  for (const cyc of cycles) {
    const arr = cyclesByCampaign.get(cyc.campaign_id) ?? [];
    arr.push(cyc);
    cyclesByCampaign.set(cyc.campaign_id, arr);
  }

  const groups: CampaignGroup[] = [];
  for (const campaign of campaigns) {
    const own = cyclesByCampaign.get(campaign.campaign_id) ?? [];
    // Partition the campaign's cycles into sessions by family root.
    const bySession = new Map<
      string,
      { root: CycleListEntry | null; branches: CycleListEntry[] }
    >();
    for (const cyc of own) {
      const sr = rootCycleId(cyc.cycle_id);
      let s = bySession.get(sr);
      if (!s) {
        s = { root: null, branches: [] };
        bySession.set(sr, s);
      }
      if (cyc.cycle_id === sr) s.root = cyc;
      else s.branches.push(cyc);
    }
    const sessions: SessionGroup[] = [];
    for (const s of bySession.values()) {
      // A session with no root cycle dir on disk can't be navigated —
      // skip it rather than render a headless branch list.
      if (!s.root) continue;
      s.branches.sort(byUpdatedDesc);
      const updatedAt = [s.root, ...s.branches].reduce(
        (m, c) => (c.updated_at > m ? c.updated_at : m),
        s.root.updated_at,
      );
      sessions.push({ root: s.root, branches: s.branches, updatedAt });
    }
    sessions.sort(
      (a, b) => sessionIndexOf(a.root.cycle_id) - sessionIndexOf(b.root.cycle_id),
    );
    const updatedAt = sessions.reduce(
      (m, s) => (s.updatedAt > m ? s.updatedAt : m),
      campaign.created_at,
    );
    groups.push({ campaign, sessions, updatedAt });
  }
  groups.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  return groups;
}

function fmtAcc(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}

// Campaign row name — the operator label when present, else the dataset
// name (the campaign IS "the {dataset} experiment").
function campaignName(c: Campaign): string {
  return c.label || c.dataset_name || c.campaign_id;
}

// We store the COLLAPSED set, not the expanded one — campaigns + sessions
// default to expanded, so empty storage = "show everything." Keys are
// prefixed (`cmp:` / `sess:`) so campaign and session ids never collide.
const COLLAPSED_STORAGE_KEY = "promptpotter.sidebar.collapsedNodes";

function loadCollapsed(): Set<string> {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function saveCollapsed(s: Set<string>) {
  try {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify([...s]));
  } catch {
    /* private mode etc. */
  }
}

const sessKey = (campaignId: string, rootId: string) =>
  `sess:${campaignId}::${rootId}`;

export function Sidebar({ onSelectCycle, onNewCycle, collapsed, onToggleCollapse }: Props) {
  // Cycle list + active pointer + current selection come from the shared
  // workspace context (one poll for the whole app). Campaigns are a
  // separate small read polled here — the campaign registry isn't on the
  // hot per-cycle path the workspace context owns.
  const { cycleId, campaignId, cycles, cyclesLoaded, activeCycleId, activeCampaignId } =
    useWorkspace();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignsLoaded, setCampaignsLoaded] = useState(false);
  // Campaign/session collapse state — nodes expand by default, so we
  // persist the ones the operator explicitly collapsed.
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(() => new Set());
  // Dataset filter — null = all datasets. Not persisted; resets per visit.
  const [datasetFilter, setDatasetFilter] = useState<string | null>(null);

  // Hydrate stored collapsed set after mount (localStorage is browser-only).
  useEffect(() => {
    setCollapsedNodes(loadCollapsed());
  }, []);

  // Poll the campaign registry alongside the workspace poll cadence.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetchCampaigns();
        if (!cancelled) setCampaigns(res.campaigns);
      } catch {
        /* sidebar still renders from the last good list */
      } finally {
        if (!cancelled) setCampaignsLoaded(true);
      }
    };
    void tick();
    const handle = window.setInterval(() => void tick(), 3000);
    const onFocus = () => void tick();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

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
      saveCollapsed(next);
      return next;
    });
  }, [focusKeys]);

  const toggleNode = useCallback((key: string) => {
    setCollapsedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      saveCollapsed(next);
      return next;
    });
  }, []);

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
      <div className="cycle-library">
        <div className="cycle-library-head">
          <span>Campaigns</span>
        </div>
        {datasetNames.length > 1 && (
          <DatasetFilterBar
            datasets={datasetNames}
            selected={datasetFilter}
            onSelect={setDatasetFilter}
          />
        )}
        {!loaded && <div className="cycle-library-note">loading…</div>}
        {loaded && groups.length === 0 && (
          <div className="cycle-library-note">
            None on disk yet — run <code>python -m promptpotter new &lt;dataset&gt;</code>.
          </div>
        )}
        {loaded && groups.length > 0 && (
          <ul className="cycle-library-list">
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
    <div className="cycle-library-filter" role="group" aria-label="Filter by dataset">
      <button
        type="button"
        className={`cycle-library-filter-chip${selected == null ? " active" : ""}`}
        onClick={() => onSelect(null)}
      >
        All
      </button>
      {datasets.map((d) => (
        <button
          key={d}
          type="button"
          className={`cycle-library-filter-chip${selected === d ? " active" : ""}`}
          onClick={() => onSelect(selected === d ? null : d)}
          title={d}
        >
          {d}
        </button>
      ))}
    </div>
  );
}

// One campaign in the flat list. A single-session campaign collapses: the
// campaign row IS that session and opens it directly (its twist, if any,
// expands the session's fork-tree). A multi-session campaign expands into
// a session row per session.
function CampaignNode({
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
      <div className={`cycle-library-family${containsViewed ? " selected" : ""}`}>
        <button
          type="button"
          className="cycle-library-twist"
          onClick={() => toggleNode(cmpKey)}
          aria-label={cmpOpen ? "Collapse sessions" : "Expand sessions"}
          aria-expanded={cmpOpen}
          tabIndex={-1}
        >
          {cmpOpen ? "▼" : "▶"}
        </button>
        <button
          type="button"
          className="cycle-library-item"
          onClick={() => toggleNode(cmpKey)}
          title={cid}
        >
          <span className="cycle-library-mark">{containsActive ? "●" : ""}</span>
          <span className="cycle-library-row">
            <span className="cycle-library-name">
              {campaignName(group.campaign)}
              {!group.campaign.label && (
                <span className="cycle-library-hash" title={cid}>
                  #{campaignOriginHash(cid).slice(0, 6)}
                </span>
              )}
            </span>
            <span className="cycle-library-meta">
              {group.sessions.length} sessions · {fmtAcc(best)}
            </span>
          </span>
        </button>
      </div>
      {cmpOpen && (
        <ul className="cycle-library-children">
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

// One session row + (when expanded) its fork-tree. Rendered either AS the
// campaign row (single-session campaign) or as a child session row
// (multi-session campaign) — `isCampaignRow` only changes the label.
function SessionSubtree({
  campaign,
  session,
  isCampaignRow,
  open,
  onToggle,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  campaign: Campaign;
  session: SessionGroup;
  isCampaignRow: boolean;
  open: boolean;
  onToggle: () => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  const cid = campaign.campaign_id;
  const root = session.root;
  const hasBranches = session.branches.length > 0;
  const selected = cid === campaignId && root.cycle_id === cycleId;
  const active = cid === activeCampaignId && root.cycle_id === activeCycleId;
  const status = root.status;
  const live = status === "running" || status === "optimizing";

  // Branch-kind chips (only on the row that owns the fork-tree twist).
  const counts = { fork: 0, sweep: 0, diag: 0 };
  for (const b of session.branches) {
    if (b.sibling_kind in counts) counts[b.sibling_kind as keyof typeof counts] += 1;
  }
  const chips = [
    { kind: "fork", glyph: "⑂", count: counts.fork },
    { kind: "sweep", glyph: "~", count: counts.sweep },
    { kind: "diag", glyph: "Δ", count: counts.diag },
  ].filter((c) => c.count > 0);

  const label = isCampaignRow
    ? campaignName(campaign)
    : `session ${sessionIndexOf(root.cycle_id)}`;

  return (
    <>
      <div className={`cycle-library-family${selected ? " selected" : ""}`}>
        <button
          type="button"
          className="cycle-library-twist"
          onClick={onToggle}
          aria-label={open ? "Collapse" : "Expand"}
          aria-expanded={open}
          disabled={!hasBranches}
          tabIndex={-1}
        >
          {!hasBranches ? "" : open ? "▼" : "▶"}
        </button>
        <button
          type="button"
          className="cycle-library-item"
          onClick={() => onSelectCycle(cid, root.cycle_id)}
          aria-current={selected ? "true" : undefined}
          title={isCampaignRow ? cid : root.cycle_id}
        >
          <span className="cycle-library-mark">{active ? "●" : ""}</span>
          <span className="cycle-library-row">
            <span className="cycle-library-name">
              {label}
              {isCampaignRow && !campaign.label && (
                <span className="cycle-library-hash" title={cid}>
                  #{campaignOriginHash(cid).slice(0, 6)}
                </span>
              )}
              {live && (
                <span className="cycle-library-live" title="Status is running">
                  ●
                </span>
              )}
            </span>
            <span className="cycle-library-meta">{fmtAcc(root.best_accuracy)}</span>
          </span>
        </button>
        {chips.length > 0 && (
          <span className="cycle-library-chips">
            {chips.map((chip) => (
              <button
                key={chip.kind}
                type="button"
                className="cycle-library-chip"
                onClick={onToggle}
                title={`${chip.count} ${chip.kind}${chip.count === 1 ? "" : "s"}`}
                aria-label={`${chip.count} ${chip.kind}`}
                tabIndex={-1}
              >
                <span className="cycle-library-chip-glyph" aria-hidden="true">
                  {chip.glyph}
                </span>
                <span className="cycle-library-chip-count">{chip.count}</span>
              </button>
            ))}
          </span>
        )}
      </div>
      {open && hasBranches && (
        <ul className="cycle-library-children">
          <UnitBranchRows
            nodes={buildUnitTree(root, session.branches).children}
            campaignId={campaignId}
            cycleId={cycleId}
            activeCampaignId={activeCampaignId}
            activeCycleId={activeCycleId}
            onSelectCycle={onSelectCycle}
          />
        </ul>
      )}
    </>
  );
}

// The fork-tree's branch rows — every fork / diag / sweep, nested under
// its parent. The session root is NOT re-rendered here (the session row
// above is the root).
function UnitBranchRows({
  nodes,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  nodes: UnitNode[];
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const u = node.unit;
        return (
          <li key={u.cycle_id}>
            <UnitRow
              unit={u}
              selected={u.campaign_id === campaignId && u.cycle_id === cycleId}
              active={
                u.campaign_id === activeCampaignId && u.cycle_id === activeCycleId
              }
              onSelect={() => onSelectCycle(u.campaign_id, u.cycle_id)}
            />
            {node.children.length > 0 && (
              <ul className="cycle-library-children">
                <UnitBranchRows
                  nodes={node.children}
                  campaignId={campaignId}
                  cycleId={cycleId}
                  activeCampaignId={activeCampaignId}
                  activeCycleId={activeCycleId}
                  onSelectCycle={onSelectCycle}
                />
              </ul>
            )}
          </li>
        );
      })}
    </>
  );
}

// One branch row inside a session's fork-tree — a fork / diag / sweep,
// carrying its kind badge and the disambiguating id tail.
function UnitRow({
  unit,
  selected,
  active,
  onSelect,
}: {
  unit: CycleListEntry;
  selected: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const live = unit.status === "running" || unit.status === "optimizing";
  const kindLabel =
    unit.unit_kind === "session" ? null : UNIT_KIND_LABEL[unit.unit_kind];
  return (
    <button
      type="button"
      className={`cycle-library-item cycle-library-child${selected ? " selected" : ""}`}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      title={unit.cycle_id}
    >
      <span className="cycle-library-mark">{active ? "●" : ""}</span>
      <span className="cycle-library-row">
        <span className="cycle-library-name">
          {shortFamilyTail(unit.cycle_id)}
          {kindLabel != null && (
            <span className="cycle-library-kind" title={`This unit is a ${kindLabel}`}>
              {kindLabel}
            </span>
          )}
          {live && <span className="cycle-library-live" title="Unit status is running">●</span>}
        </span>
        <span className="cycle-library-meta">{fmtAcc(unit.best_accuracy)}</span>
      </span>
    </button>
  );
}
