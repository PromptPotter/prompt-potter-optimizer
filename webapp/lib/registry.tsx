"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import {
  activeRead,
  hierarchyRead,
  type HierarchyCampaign,
  type HierarchyCourse,
  type HierarchyEntry,
  type LifecycleFilter,
} from "./api";
import { coursesOf, runningCourses } from "./derivations/hierarchy";
import { shownData, useRead, type ReadFailure } from "./hooks/useRead";

interface Registry {
  entries: HierarchyCampaign[];
  byAddress: ReadonlyMap<string, HierarchyEntry>;
  running: HierarchyCourse[];
  // False from a filter change until the read for the new filter lands.
  loaded: boolean;
  failure: ReadFailure | null;
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
}

const RegistryContext = createContext<Registry | null>(null);

const REGISTRY_INTERVAL_MS = 10000;
// Matches the dashboard's live beat, so a CLI-minted cycle is followed without the registry's lag.
const POINTER_INTERVAL_MS = 2000;

const NO_ENTRIES: HierarchyCampaign[] = [];

export function RegistryProvider({ children }: { children: ReactNode }) {
  const [lifecycleFilter, setLifecycleFilter] = useState<LifecycleFilter>("active");
  const read = useRead(hierarchyRead(lifecycleFilter), {
    auth: true,
    intervalMs: REGISTRY_INTERVAL_MS,
  });

  const landed = shownData(read)?.entries ?? null;
  const [lastEntries, setLastEntries] = useState<HierarchyCampaign[]>(NO_ENTRIES);
  if (landed !== null && landed !== lastEntries) setLastEntries(landed);
  const entries = landed ?? lastEntries;

  const byAddress = useMemo(() => {
    const m = new Map<string, HierarchyEntry>();
    for (const campaign of entries) {
      m.set(campaign.address, campaign);
      for (const course of coursesOf(campaign)) m.set(course.address, course);
    }
    return m;
  }, [entries]);
  const running = useMemo(() => runningCourses(entries), [entries]);

  const failure = read.status === "failed" ? read.failure : null;
  const loaded = landed !== null;

  const value = useMemo<Registry>(
    () => ({
      entries,
      byAddress,
      running,
      loaded,
      failure,
      lifecycleFilter,
      setLifecycleFilter,
    }),
    [entries, byAddress, running, loaded, failure, lifecycleFilter],
  );
  return <RegistryContext.Provider value={value}>{children}</RegistryContext.Provider>;
}

export function useRegistry(): Registry {
  const v = useContext(RegistryContext);
  if (!v) throw new Error("useRegistry must be called inside <RegistryProvider>");
  return v;
}

export function useEntry(address: string | null): HierarchyEntry | null {
  const { byAddress } = useRegistry();
  return address === null ? null : (byAddress.get(address) ?? null);
}

export interface ActivePointer {
  campaignId: string | null;
  cycleId: string | null;
  // "No active session" is null ids on a 200 (`active.py::get_active_session`), never this.
  failure: ReadFailure | null;
}

// The tenant's LATEST launch, not the live set.
export function useActivePointer(): ActivePointer {
  const read = useRead(activeRead(), { auth: true, intervalMs: POINTER_INTERVAL_MS });
  const active = shownData(read);
  const campaignId = active?.campaign_id || null;
  const cycleId = active?.cycle_id || null;
  const failure = read.status === "failed" ? read.failure : null;
  return useMemo(
    () => ({ campaignId, cycleId, failure }),
    [campaignId, cycleId, failure],
  );
}
