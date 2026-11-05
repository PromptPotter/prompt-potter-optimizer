import type { HierarchyCampaign, ListedCampaign } from "@/lib/api/types";
import { createModuleStore } from "./module-store";

// What a filter can pick is a state a campaign is in: the wire's "all" would list nothing here.
export type ListedLifecycle = HierarchyCampaign["facts"]["lifecycle"];

// The sidebar's lifecycle filter: transient, so a reload lists the active again.
const store = createModuleStore<{ lifecycle: ListedLifecycle }>({ lifecycle: "active" });

export function setListedLifecycle(lifecycle: ListedLifecycle): void {
  store.set({ lifecycle });
}

export function useListedLifecycle(): ListedLifecycle {
  return store.useStore().lifecycle;
}

// The active list leads with the unstarted: a setup waiting for Start is never behind a filter.
export function listedUnder(lifecycle: ListedLifecycle, campaign: ListedCampaign): boolean {
  return campaign.level === "unstarted"
    ? lifecycle === "active"
    : campaign.facts.lifecycle === lifecycle;
}
