"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import {
  activeRead,
  hierarchyRead,
  type HierarchyCampaign,
  type HierarchyCourse,
  type HierarchyEntry,
  type ListedCampaign,
  type UnstartedCampaign,
} from "@/lib/api";
import { ACTIVE_POINTER_POLL_MS, REGISTRY_POLL_MS } from "@/lib/cadence";
import { campaignAt, runningCourses } from "../derivations/hierarchy";
import { shownData, useRead, type ReadFailure } from "@/lib/hooks/useRead";

interface Registry {
  // Every lifecycle, so an address resolves whatever the filter lists; the unstarted lead.
  entries: ListedCampaign[];
  // The sidebar's and the switcher's list: `entries` under the lifecycle filter.
  listed: ListedCampaign[];
  // `entries` with a run: the ones `byAddress` holds, with their courses.
  started: HierarchyCampaign[];
  // The open check-ins, most recently edited first; each `address` is its draft id.
  unstarted: UnstartedCampaign[];
  // An unstarted campaign is reached by its check-in, never an address here.
  byAddress: ReadonlyMap<string, HierarchyEntry>;
  running: HierarchyCourse[];
  loaded: boolean;
  failure: ReadFailure | null;
  listedLifecycle: ListedLifecycle;
  setListedLifecycle: (f: ListedLifecycle) => void;
}

// What a filter can pick is a state a campaign is in: the wire's "all" would list nothing here.
export type ListedLifecycle = HierarchyCampaign["facts"]["lifecycle"];

const RegistryContext = createContext<Registry | null>(null);

const NO_ENTRIES: ListedCampaign[] = [];

// The active list leads with the unstarted: a setup waiting for Start is never behind a filter.
function listedUnder(lifecycle: ListedLifecycle, campaign: ListedCampaign): boolean {
  return campaign.level === "unstarted"
    ? lifecycle === "active"
    : campaign.facts.lifecycle === lifecycle;
}

export function RegistryProvider({ children }: { children: ReactNode }) {
  const [listedLifecycle, setListedLifecycle] = useState<ListedLifecycle>("active");
  const read = useRead(hierarchyRead(), {
    auth: true,
    intervalMs: REGISTRY_POLL_MS,
  });

  const landed = shownData(read)?.entries ?? null;
  const [lastEntries, setLastEntries] = useState<ListedCampaign[]>(NO_ENTRIES);
  if (landed !== null && landed !== lastEntries) setLastEntries(landed);
  const entries = landed ?? lastEntries;

  const started = useMemo(
    () => entries.filter((c): c is HierarchyCampaign => c.level === "campaign"),
    [entries],
  );
  const unstarted = useMemo(
    () => entries.filter((c): c is UnstartedCampaign => c.level === "unstarted"),
    [entries],
  );
  const byAddress = useMemo(() => {
    const m = new Map<string, HierarchyEntry>();
    for (const campaign of started) {
      m.set(campaign.address, campaign);
      for (const course of campaign.children) m.set(course.address, course);
    }
    return m;
  }, [started]);
  const running = useMemo(() => runningCourses(started), [started]);
  const listed = useMemo(
    () => entries.filter((c) => listedUnder(listedLifecycle, c)),
    [entries, listedLifecycle],
  );

  const failure = read.status === "failed" ? read.failure : null;
  const loaded = landed !== null;

  const value = useMemo<Registry>(
    () => ({
      entries,
      listed,
      started,
      unstarted,
      byAddress,
      running,
      loaded,
      failure,
      listedLifecycle,
      setListedLifecycle,
    }),
    [entries, listed, started, unstarted, byAddress, running, loaded, failure, listedLifecycle],
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

export function useCampaign(address: string | null): HierarchyCampaign | null {
  const { byAddress } = useRegistry();
  return address === null ? null : campaignAt(byAddress, address);
}

interface ActivePointer {
  campaignId: string | null;
  cycleId: string | null;
  // "No active session" is null ids on a 200 (`active.py::get_active_session`), never this.
  failure: ReadFailure | null;
}

// The tenant's LATEST launch, not the live set.
export function useActivePointer(): ActivePointer {
  const read = useRead(activeRead(), { auth: true, intervalMs: ACTIVE_POINTER_POLL_MS });
  const active = shownData(read);
  const campaignId = active?.campaign_id || null;
  const cycleId = active?.cycle_id || null;
  const failure = read.status === "failed" ? read.failure : null;
  return useMemo(
    () => ({ campaignId, cycleId, failure }),
    [campaignId, cycleId, failure],
  );
}
