"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { isNodeOpen, nodeKey, type NodeKind } from "@/lib/derivations";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { ownerOfNodeAddress } from "@/lib/ids";

export const VIEW_MEMORY_KEY = "promptpotter.view.byCampaign";

// Bumping this DROPS every stored record rather than migrating it.
const RECORD_VERSION = 1;

// Past two weeks a stored expansion set describes a tree that has since grown forks and inner runs.
const TTL_MS = 14 * 24 * 60 * 60 * 1000;

const MAX_CAMPAIGNS = 24;

// Ids, flags and UI keys only — never a measurement. The card's headline `metrics` is not stored:
// `CandidatesCard` seeds it per cycle from the evaluators the run actually produced.
export interface CampaignView {
  v: number;
  // Drives both the TTL and the LRU eviction order.
  at: number;
  // Sidebar nodes TOGGLED AWAY FROM THEIR DEFAULT (`campaign-forest.ts::isNodeOpen`).
  toggled: string[];
  // Latches the one-shot "reveal the running course", or a sidebar remount re-opens a row the
  // operator deliberately collapsed.
  autoExpandedFor: string | null;
  viewedPath: string | null;
  viewedCandidateId: string | null;
  showForest: boolean;
  // Lane keys (`nodeKeyOf`), the same space `forest-layout::layout` matches.
  expandedLanes: string[];
}

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

// TTL + version drop + LRU live in the codec, so no caller can forget them.
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

// `owner` is a campaign id everywhere but the sidebar's origin tier (see `NodeToggle`).
interface ViewMemory {
  viewFor: (owner: string | null) => CampaignView;
  // Built once per store change: `isOpen` runs per sidebar row per render.
  toggledFor: (owner: string | null) => ReadonlySet<string>;
  // Called from the handler that changed the axis, never during render.
  recordView: (owner: string | null, patch: Partial<CampaignView>) => void;
}

const ViewMemoryContext = createContext<ViewMemory | null>(null);

const EMPTY_TOGGLED: ReadonlySet<string> = new Set();

export function ViewMemoryProvider({ children }: { children: ReactNode }) {
  const [store, setStore] = useLocalStorage<Store>(VIEW_MEMORY_KEY, EMPTY_STORE, viewMemoryCodec);

  const viewFor = useCallback(
    (owner: string | null): CampaignView => {
      if (!owner) return emptyView();
      return store[owner] ?? emptyView();
    },
    [store],
  );

  const toggledByOwner = useMemo(() => {
    const m = new Map<string, ReadonlySet<string>>();
    for (const [key, view] of Object.entries(store)) m.set(key, new Set(view.toggled));
    return m;
  }, [store]);
  const toggledFor = useCallback(
    (owner: string | null): ReadonlySet<string> =>
      (owner ? toggledByOwner.get(owner) : undefined) ?? EMPTY_TOGGLED,
    [toggledByOwner],
  );

  const recordView = useCallback(
    (owner: string | null, patch: Partial<CampaignView>) => {
      if (!owner) return;
      setStore((prev) => ({
        ...prev,
        [owner]: {
          ...(prev[owner] ?? emptyView()),
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

// Owner is the ROOT hop's campaign for a course or candidate; an ORIGIN spans campaigns, so it owns
// its own record under its `cycle_<hash>` id (`ownerOfNodeAddress`).
export interface NodeToggle {
  isOpen: (kind: NodeKind, path: string) => boolean;
  toggle: (kind: NodeKind, path: string) => void;
  autoExpandedFor: (campaignId: string | null) => string | null;
  markAutoExpanded: (campaignId: string, cycleId: string, key: string) => void;
}

export function useNodeToggle(): NodeToggle {
  const { viewFor, toggledFor, recordView } = useViewMemory();

  const isOpen = useCallback(
    (kind: NodeKind, path: string) => isNodeOpen(toggledFor(ownerOfNodeAddress(path)), kind, path),
    [toggledFor],
  );

  const toggle = useCallback(
    (kind: NodeKind, path: string) => {
      const owner = ownerOfNodeAddress(path);
      if (!owner) return;
      const key = nodeKey(kind, path);
      const toggled = new Set(toggledFor(owner));
      if (toggled.has(key)) toggled.delete(key);
      else toggled.add(key);
      recordView(owner, { toggled: [...toggled] });
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
