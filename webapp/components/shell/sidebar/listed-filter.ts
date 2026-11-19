import type { HierarchyCampaign, ListedCampaign } from "@/lib/api/types";
import { tagValue } from "@/lib/derivations";
import { createModuleStore } from "@/lib/state/module-store";

// What a filter can pick is a state a campaign is in: the wire's "all" would list nothing here.
export type ListedLifecycle = HierarchyCampaign["facts"]["lifecycle"];

interface ListedFilter {
  lifecycle: ListedLifecycle;
  dataset: string | null;
}

const UNFILTERED: ListedFilter = { lifecycle: "active", dataset: null };

// The sidebar's filter: transient, so a reload lists every active campaign again.
const store = createModuleStore<ListedFilter>(UNFILTERED);

export const useListedFilter = store.useStore;
export const setListedFilter = store.set;

export function clearListedFilter(): void {
  store.set(UNFILTERED);
}

export function isFiltered(filter: ListedFilter): boolean {
  return filter.lifecycle !== UNFILTERED.lifecycle || filter.dataset !== UNFILTERED.dataset;
}

export function datasetOf(campaign: ListedCampaign): string {
  return tagValue(campaign, "dataset") || "(unknown)";
}

// The active list leads with the unstarted: a setup waiting for Start is never behind a filter.
export function listedUnder(lifecycle: ListedLifecycle, campaign: ListedCampaign): boolean {
  return campaign.entry_kind === "unstarted"
    ? lifecycle === "active"
    : campaign.facts.lifecycle === lifecycle;
}
