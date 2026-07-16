// A campaign's forks, keyed by the cycle each was cut from — THE children-map of
// the lineage tree's course tier. Both renderings of that tree read it: the
// sidebar draws a fork as a sibling course row beside its candidates, the
// candidates card as one bar after them. A fork of a fork files under its own
// parent by the same rule, which is what lets one map serve every depth.
// Buckets are sorted most-recently-updated first, same as the sidebar's rows.

import type { CycleListEntry } from "@/lib/api";

export type ForkIndex = ReadonlyMap<string, CycleListEntry[]>;

export function indexForks(cycles: readonly CycleListEntry[]): ForkIndex {
  const byParent = new Map<string, CycleListEntry[]>();
  for (const c of cycles) {
    if (!c.parent_cycle_id) continue;
    const arr = byParent.get(c.parent_cycle_id) ?? [];
    arr.push(c);
    byParent.set(c.parent_cycle_id, arr);
  }
  for (const arr of byParent.values()) {
    arr.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  }
  return byParent;
}
