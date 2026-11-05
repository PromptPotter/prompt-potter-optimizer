import { useMemo } from "react";
import { activeRead, hierarchyRead } from "@/lib/api/reads";
import type {
  ActiveSessionResponse,
  HierarchyCampaign,
  HierarchyCourse,
  HierarchyEntry,
  HierarchyResponse,
  ListedCampaign,
  UnstartedCampaign,
} from "@/lib/api/types";
import { campaignAt, runningCourses } from "../derivations/hierarchy";
import type { ReadResult } from "@/lib/read-cache";
import { shownData, useRead } from "./useRead";

interface RegistryIndex {
  // Every lifecycle, so an address resolves whatever the filter lists; the unstarted lead.
  entries: readonly ListedCampaign[];
  // `entries` with a run: the ones `byAddress` holds, with their courses.
  started: readonly HierarchyCampaign[];
  // The open check-ins, most recently edited first; each `address` is its draft id.
  unstarted: readonly UnstartedCampaign[];
  // An unstarted campaign is reached by its check-in, never an address here.
  byAddress: ReadonlyMap<string, HierarchyEntry>;
  running: readonly HierarchyCourse[];
}

const EMPTY: RegistryIndex = {
  entries: [],
  started: [],
  unstarted: [],
  byAddress: new Map(),
  running: [],
};

// Keyed on the body it read: every caller of one listing shares one index.
const indexed = new WeakMap<HierarchyResponse, RegistryIndex>();

function indexOf(body: HierarchyResponse | null): RegistryIndex {
  if (body === null) return EMPTY;
  const hit = indexed.get(body);
  if (hit) return hit;
  const entries = body.entries;
  const started = entries.filter((c): c is HierarchyCampaign => c.level === "campaign");
  const byAddress = new Map<string, HierarchyEntry>();
  for (const campaign of started) {
    byAddress.set(campaign.address, campaign);
    for (const course of campaign.children) byAddress.set(course.address, course);
  }
  const index: RegistryIndex = {
    entries,
    started,
    unstarted: entries.filter((c): c is UnstartedCampaign => c.level === "unstarted"),
    byAddress,
    running: runningCourses(started),
  };
  indexed.set(body, index);
  return index;
}

export function useRegistry(): RegistryIndex & { read: ReadResult<HierarchyResponse> } {
  const read = useRead(hierarchyRead());
  const index = indexOf(shownData(read));
  return useMemo(() => ({ read, ...index }), [read, index]);
}

export function useEntry(address: string | null): HierarchyEntry | null {
  const { byAddress } = useRegistry();
  return address === null ? null : (byAddress.get(address) ?? null);
}

export function useCampaign(address: string | null): HierarchyCampaign | null {
  const { byAddress } = useRegistry();
  return address === null ? null : campaignAt(byAddress, address);
}

// The latest launch's cycle, under the served names: both ids, or none.
export type ActiveCycle = { [K in "campaign_id" | "cycle_id"]: NonNullable<ActiveSessionResponse[K]> };

interface ActivePointer {
  read: ReadResult<ActiveSessionResponse>;
  // Null while none is: "no active session" is null ids on a 200
  // (`cycle_addressing.py::active_session`), never a failure.
  active: ActiveCycle | null;
}

export function useActivePointer(): ActivePointer {
  const read = useRead(activeRead());
  const body = shownData(read);
  const campaignId = body?.campaign_id ?? null;
  const cycleId = body?.cycle_id ?? null;
  const active = useMemo(
    () => (campaignId && cycleId ? { campaign_id: campaignId, cycle_id: cycleId } : null),
    [campaignId, cycleId],
  );
  return { read, active };
}
