"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { ownerOfAddress } from "@/lib/ids";

const VIEW_MEMORY_KEY = "promptpotter.view.byCampaign";

// Bumping this DROPS every stored record rather than migrating it.
const RECORD_VERSION = 2;

const TTL_MS = 14 * 24 * 60 * 60 * 1000;

const MAX_CAMPAIGNS = 24;

// Ids, flags and UI keys only — never a measurement.
export interface CampaignView {
  v: number;
  at: number;
  // Sidebar rows toggled AWAY from the default their call site passes to `isOpen`.
  toggled: string[];
  // Latches the one-shot reveal, or a sidebar remount re-opens a row the operator collapsed.
  autoExpandedFor: string | null;
  viewedPath: string | null;
  viewedCandidateId: string | null;
  showForest: boolean;
  // Lane keys (course addresses), the same space `forest-layout::layout` matches.
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
  viewFor: (owner: string | null) => CampaignView;
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

// An ORIGIN spans campaigns, so it owns a record under its own `cycle_<hash>` id (`ownerOfAddress`).
export interface NodeToggle {
  isOpen: (key: string, openByDefault: boolean) => boolean;
  toggle: (key: string) => void;
  autoExpandedFor: (campaignId: string | null) => string | null;
  markAutoExpanded: (campaignId: string, cycleId: string, key: string) => void;
}

export function useNodeToggle(): NodeToggle {
  const { viewFor, toggledFor, recordView } = useViewMemory();

  const isOpen = useCallback(
    (key: string, openByDefault: boolean) =>
      toggledFor(ownerOfAddress(key)).has(key) ? !openByDefault : openByDefault,
    [toggledFor],
  );

  const toggle = useCallback(
    (key: string) => {
      const owner = ownerOfAddress(key);
      if (!owner) return;
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
