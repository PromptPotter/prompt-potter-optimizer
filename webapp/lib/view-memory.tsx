"use client";
// Per-campaign view memory — "put me back where I was, if I was here recently".
//
// This REPLACES the single global `promptpotter.sidebar.collapsedNodes` blob, which was
// one Set for every campaign at once, never pruned, and carried nothing but the sidebar's
// expand/collapse. Everything else an operator arranges — which candidate they were
// inspecting, which round, whether the forest was open, which lanes were expanded — was
// dropped on every campaign switch and rebuilt by hand on the way back.
//
// The blob had to be global for one reason: `collapsedNodes` was a flat Set with no
// campaign in it. But a node key is `{kind}:{encodedPath}`, and every path opens with its
// own `(campaignId, cycleId)` hop — so the keys were ALREADY campaign-partitioned and the
// Set never needed to be shared. Splitting per campaign is what makes the rest of this
// possible; it is not a new mechanism beside the old one.
//
// WHAT IS DELIBERATELY NOT REMEMBERED, and why — the rule is that memory may restore a
// VIEW, never a claim about a measurement:
//   - `lens` / what-if weights / `sampleSet`. A lens is a counterfactual the operator
//     opened to ask a question. Silently restoring one means a returning operator reads
//     masked numbers believing they are the record. `lib/lineage.tsx` resets the lens per
//     campaign on purpose; that stays.
//   - `datasetFilter` / `lifecycleFilter` — workspace-scoped, not campaign-scoped.
//   - `following` — restoring a pin would fight the active-pointer auto-snap
//     (`workspace.tsx`), which exists so a CLI-minted run yanks the view over.
//
// NOTHING STORED HERE IS A MEASUREMENT. Every field is an id, a boolean, or a UI key, so a
// restore can never render a number the operator reads as current. That rules out the
// INSPECTION axis (`SelectionContext.candidate`): it carries `accuracy` and `is_winner`,
// and `ScoringInspector` renders `is_winner` — restoring a stored one would claim a
// candidate won a round it may since have lost. Re-deriving it needs the served tree, which
// arrives after this record does. What IS restored is the NAVIGATION axis — `viewedPath` +
// `viewedCandidateId` — which parks the tree on the same node and re-plots the same bars;
// only the lit bar and the inspector come back empty.

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { isNodeOpen, nodeKey, type NodeKind } from "@/components/shell/sidebar/grouping";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { decodeCyclePath } from "@/lib/ids";

export const VIEW_MEMORY_KEY = "promptpotter.view.byCampaign";

// Bumping this DROPS every stored record rather than migrating it. There is nothing to be
// compatible with — a lost expand state costs one click, and a migration path for view
// memory is more code than the thing it protects.
const RECORD_VERSION = 1;

// "Recently" — 14 days. Past roughly two weeks a stored expansion set describes a tree that
// has since grown forks and inner runs, so restoring it is likelier to confuse than help,
// and the sidebar's own defaults (courses closed) are a safe resting state. Two weeks also
// covers the longest absence where an operator still remembers the layout they left.
const TTL_MS = 14 * 24 * 60 * 60 * 1000;

// Campaigns kept, most-recently-viewed first. One record is ~0.5–2 KB (the toggled key list
// dominates), so 24 sits far under any quota while covering more campaigns than a sidebar
// shows without scrolling.
const MAX_CAMPAIGNS = 24;

export interface CampaignView {
  v: number;
  // Epoch ms of the last visit — drives both the TTL and the LRU eviction order.
  at: number;
  // Sidebar nodes TOGGLED AWAY FROM THEIR DEFAULT (`grouping.ts::isNodeOpen`), this
  // campaign's only. Same semantics the global blob had, minus the sharing.
  toggled: string[];
  // The active cycle whose one-shot "reveal the running course" already fired. Without
  // this the reveal re-fires whenever the sidebar remounts, re-opening a row the operator
  // deliberately collapsed.
  autoExpandedFor: string | null;
  // The viewed address, encoded — restores a drilled-in L4 leaf, not just the campaign.
  viewedPath: string | null;
  viewedCandidateId: string | null;
  showForest: boolean;
  // Candidates-card lane keys (`nodeKeyOf`), the same space `forest-layout::layout` matches.
  expandedLanes: string[];
}

// NOT stored: the card's headline `metrics`. `CandidatesCard` seeds that per cycle from the
// evaluators the run actually produced (`metricsSeededForCycle`), so a remembered set could
// name evaluators this campaign never had — and the seeding already owns the axis.

type Store = Record<string, CampaignView>;

const EMPTY_STORE: Store = {};

export function emptyView(): CampaignView {
  return {
    v: RECORD_VERSION,
    at: 0,
    toggled: [],
    autoExpandedFor: null,
    viewedPath: null,
    viewedCandidateId: null,
    showForest: false,
    expandedLanes: [],
  };
}

// TTL + version drop + LRU, applied in ONE place: the codec. Every read prunes and every
// write re-prunes, so no caller can forget and no separate sweep exists to fall out of sync.
export function pruneStore(store: Store, now: number): Store {
  const fresh = Object.entries(store).filter(
    ([, v]) => v && v.v === RECORD_VERSION && now - v.at < TTL_MS,
  );
  fresh.sort((a, b) => b[1].at - a[1].at);
  return Object.fromEntries(fresh.slice(0, MAX_CAMPAIGNS));
}

export const viewMemoryCodec = {
  serialize: (s: Store) => JSON.stringify(pruneStore(s, Date.now())),
  deserialize: (raw: string): Store => {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return EMPTY_STORE;
    return pruneStore(parsed as Store, Date.now());
  },
};

interface ViewMemory {
  // The record for a campaign — defaults when absent, expired, or a stale version.
  viewFor: (campaignId: string | null) => CampaignView;
  // A campaign's toggled set, as a Set. `isOpen` runs per sidebar row per render, so the
  // array→Set build happens once per store change here, not per call.
  toggledFor: (campaignId: string | null) => ReadonlySet<string>;
  // Merge a patch into a campaign's record and stamp `at`. Called from the handler that
  // changed the axis, never during render.
  recordView: (campaignId: string | null, patch: Partial<CampaignView>) => void;
}

const ViewMemoryContext = createContext<ViewMemory | null>(null);

const EMPTY_TOGGLED: ReadonlySet<string> = new Set();

export function ViewMemoryProvider({ children }: { children: ReactNode }) {
  const [store, setStore] = useLocalStorage<Store>(VIEW_MEMORY_KEY, EMPTY_STORE, viewMemoryCodec);

  const viewFor = useCallback(
    (campaignId: string | null): CampaignView => {
      if (!campaignId) return emptyView();
      return store[campaignId] ?? emptyView();
    },
    [store],
  );

  const toggledByCampaign = useMemo(() => {
    const m = new Map<string, ReadonlySet<string>>();
    for (const [cid, view] of Object.entries(store)) m.set(cid, new Set(view.toggled));
    return m;
  }, [store]);
  const toggledFor = useCallback(
    (campaignId: string | null): ReadonlySet<string> =>
      (campaignId ? toggledByCampaign.get(campaignId) : undefined) ?? EMPTY_TOGGLED,
    [toggledByCampaign],
  );

  const recordView = useCallback(
    (campaignId: string | null, patch: Partial<CampaignView>) => {
      if (!campaignId) return;
      setStore((prev) => ({
        ...prev,
        [campaignId]: {
          ...(prev[campaignId] ?? emptyView()),
          ...patch,
          v: RECORD_VERSION,
          at: Date.now(),
        },
      }));
    },
    [setStore],
  );

  const value = useMemo<ViewMemory>(
    () => ({ viewFor, toggledFor, recordView }),
    [viewFor, toggledFor, recordView],
  );
  return <ViewMemoryContext.Provider value={value}>{children}</ViewMemoryContext.Provider>;
}

export function useViewMemory(): ViewMemory {
  const ctx = useContext(ViewMemoryContext);
  if (ctx === null) throw new Error("useViewMemory must be used within a ViewMemoryProvider");
  return ctx;
}

// The sidebar's expand/collapse, resolved per campaign.
//
// A node key is `{kind}:{encodedPath}` and every path opens with its own
// `(campaignId, cycleId)` hop, so the campaign that owns a node is read straight off its
// address — no second lookup, and no way for one campaign's toggles to reach another's.
// That is what let the old blob be global without being obviously wrong.
export interface NodeToggle {
  isOpen: (kind: NodeKind, path: string) => boolean;
  toggle: (kind: NodeKind, path: string) => void;
  // The one-shot "reveal the active run" latch, per campaign — see `CampaignView`.
  autoExpandedFor: (campaignId: string | null) => string | null;
  markAutoExpanded: (campaignId: string, cycleId: string, key: string) => void;
}

export function useNodeToggle(): NodeToggle {
  const { viewFor, toggledFor, recordView } = useViewMemory();

  const isOpen = useCallback(
    (kind: NodeKind, path: string) =>
      isNodeOpen(toggledFor(campaignOfEncodedPath(path)), kind, path),
    [toggledFor],
  );

  const toggle = useCallback(
    (kind: NodeKind, path: string) => {
      const campaignId = campaignOfEncodedPath(path);
      if (!campaignId) return;
      const key = nodeKey(kind, path);
      const toggled = new Set(toggledFor(campaignId));
      if (toggled.has(key)) toggled.delete(key);
      else toggled.add(key);
      recordView(campaignId, { toggled: [...toggled] });
    },
    [toggledFor, recordView],
  );

  const autoExpandedFor = useCallback(
    (campaignId: string | null) => viewFor(campaignId).autoExpandedFor,
    [viewFor],
  );

  const markAutoExpanded = useCallback(
    (campaignId: string, cycleId: string, key: string) => {
      const toggled = new Set(toggledFor(campaignId));
      toggled.add(key);
      recordView(campaignId, { toggled: [...toggled], autoExpandedFor: cycleId });
    },
    [toggledFor, recordView],
  );

  return useMemo(
    () => ({ isOpen, toggle, autoExpandedFor, markAutoExpanded }),
    [isOpen, toggle, autoExpandedFor, markAutoExpanded],
  );
}

// The campaign a node key's address belongs to — its ROOT hop, because that is the campaign
// whose sidebar row owns the whole subtree (an L4 inner run is filed under it, in its
// sandbox, and is not a campaign an operator browses to on its own).
function campaignOfEncodedPath(encoded: string): string | null {
  return decodeCyclePath(encoded)?.[0]?.campaignId ?? null;
}
