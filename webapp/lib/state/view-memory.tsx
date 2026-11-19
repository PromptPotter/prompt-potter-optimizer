import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { STORAGE_KEYS } from "@/lib/storage-keys";
import { ownerOfAddress } from "@/lib/ids";

// Bumping this DROPS every stored record rather than migrating it.
const RECORD_VERSION = 6;

const TTL_MS = 14 * 24 * 60 * 60 * 1000;

const MAX_CAMPAIGNS = 24;

// Ids, flags and UI keys only — never a measurement.
export interface CampaignView {
  v: number;
  at: number;
  // Sidebar rows the operator opened: every row starts closed.
  opened: string[];
  viewedPath: string | null;
  // `address.ts::formatPick`'s spelling.
  viewedPick: string | null;
  treeFreed: boolean;
  // Lane keys (cycle addresses), the same space `lineage-tree.ts::layout` matches.
  expandedLanes: string[];
  // The viewed lane last opened for its visit: a manual collapse is not undone.
  expandedForLane: string | null;
}

type Store = Record<string, CampaignView>;

const EMPTY_STORE: Store = {};

export function emptyView(): CampaignView {
  return {
    v: RECORD_VERSION,
    at: 0,
    opened: [],
    viewedPath: null,
    viewedPick: null,
    treeFreed: false,
    expandedLanes: [],
    expandedForLane: null,
  };
}

export function pruneStore(store: Readonly<Record<string, CampaignView | null>>, now: number): Store {
  const fresh = Object.entries(store).filter(
    (e): e is [string, CampaignView] =>
      e[1] !== null && e[1].v === RECORD_VERSION && now - e[1].at < TTL_MS,
  );
  fresh.sort((a, b) => b[1].at - a[1].at);
  return Object.fromEntries(fresh.slice(0, MAX_CAMPAIGNS));
}

export const viewMemoryCodec = {
  serialize: (s: Store) => JSON.stringify(pruneStore(s, Date.now())),
  deserialize: (raw: string): Store => {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return EMPTY_STORE;
    return pruneStore(parsed as Record<string, CampaignView | null>, Date.now());
  },
};

interface ViewMemory {
  viewFor: (owner: string | null) => CampaignView;
  openedFor: (owner: string | null) => ReadonlySet<string>;
  // Called from the handler that changed the axis, never during render.
  recordView: (owner: string | null, patch: Partial<CampaignView>) => void;
}

const ViewMemoryContext = createContext<ViewMemory | null>(null);

const EMPTY_OPENED: ReadonlySet<string> = new Set();

export function ViewMemoryProvider({ children }: { children: ReactNode }) {
  const [store, setStore] = useLocalStorage<Store>(STORAGE_KEYS.viewMemory, EMPTY_STORE, viewMemoryCodec);

  const viewFor = useCallback(
    (owner: string | null): CampaignView => {
      if (!owner) return emptyView();
      return store[owner] ?? emptyView();
    },
    [store],
  );

  const openedByOwner = useMemo(() => {
    const m = new Map<string, ReadonlySet<string>>();
    for (const [key, view] of Object.entries(store)) m.set(key, new Set(view.opened));
    return m;
  }, [store]);
  const openedFor = useCallback(
    (owner: string | null): ReadonlySet<string> =>
      (owner ? openedByOwner.get(owner) : undefined) ?? EMPTY_OPENED,
    [openedByOwner],
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
    () => ({ viewFor, openedFor, recordView }),
    [viewFor, openedFor, recordView],
  );
  return <ViewMemoryContext.Provider value={value}>{children}</ViewMemoryContext.Provider>;
}

export function useViewMemory(): ViewMemory {
  const ctx = useContext(ViewMemoryContext);
  if (ctx === null) throw new Error("useViewMemory must be used within a ViewMemoryProvider");
  return ctx;
}

interface NodeToggle {
  isOpen: (key: string) => boolean;
  toggle: (key: string) => void;
}

export function useNodeToggle(): NodeToggle {
  const { openedFor, recordView } = useViewMemory();

  const isOpen = useCallback(
    (key: string) => openedFor(ownerOfAddress(key)).has(key),
    [openedFor],
  );

  const toggle = useCallback(
    (key: string) => {
      const owner = ownerOfAddress(key);
      if (!owner) return;
      const opened = new Set(openedFor(owner));
      if (opened.has(key)) opened.delete(key);
      else opened.add(key);
      recordView(owner, { opened: [...opened] });
    },
    [openedFor, recordView],
  );

  return useMemo(() => ({ isOpen, toggle }), [isOpen, toggle]);
}
