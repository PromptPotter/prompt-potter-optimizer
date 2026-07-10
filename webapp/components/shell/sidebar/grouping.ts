// Sidebar data model: the campaign → fork-tree grouping logic and the
// collapsed-node persistence codec. Pure — no React; the Sidebar tree-row
// components own the rendering.

import type { CampaignSummary, CycleListEntry } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// One campaign's row in the tree: the manifest, its single root cycle, and
// every fork / diag / sweep that descends from it. A campaign mints exactly
// one root (`{dataset}__{rand6}` per `new`), so there is no session tier.
export interface CampaignGroup {
  campaign: CampaignSummary;
  root: CycleListEntry;
  branches: CycleListEntry[];
  // Most-recent updated_at across every cycle — sorts campaigns so the one
  // being actively worked on stays at the top.
  updatedAt: string;
  // Best fitness across the whole fork-tree (root + branches) — the winner
  // often lives in a fork, so this is NOT just the root's number.
  bestAccuracy: number | null;
}

// Best fitness across a set of cycles — max of the non-null best_accuracy
// values, or null when none has scored a round yet (renders as "—"). A fresh
// sibling carries 0.0 (a real value); a no-rounds-yet cycle carries null, so
// the `!= null` guard keeps "—" distinct from a genuine 0%.
function bestAccuracyOf(entries: CycleListEntry[]): number | null {
  let best: number | null = null;
  for (const e of entries) {
    if (e.best_accuracy != null && (best == null || e.best_accuracy > best)) {
      best = e.best_accuracy;
    }
  }
  return best;
}

// One node in a campaign's fork-tree: a unit plus the units forked off it.
export interface UnitNode {
  unit: CycleListEntry;
  children: UnitNode[];
}

const byUpdatedDesc = (a: CycleListEntry, b: CycleListEntry) =>
  a.updated_at < b.updated_at ? 1 : -1;

// Build a campaign's fork-tree from `parent_cycle_id`. The root is the
// trunk; every branch nests under its parent. A branch whose parent isn't
// in the campaign attaches to the root so it can never vanish.
export function buildUnitTree(
  root: CycleListEntry,
  branches: CycleListEntry[],
): UnitNode {
  const rootId = root.cycle_id;
  const childrenOf = new Map<string, CycleListEntry[]>();
  for (const u of branches) {
    const parent =
      u.parent_cycle_id &&
      (u.parent_cycle_id === rootId ||
        branches.some((x) => x.cycle_id === u.parent_cycle_id))
        ? u.parent_cycle_id
        : rootId;
    const arr = childrenOf.get(parent) ?? [];
    arr.push(u);
    childrenOf.set(parent, arr);
  }
  const visit = (unit: CycleListEntry): UnitNode => ({
    unit,
    children: (childrenOf.get(unit.cycle_id) ?? [])
      .slice()
      .sort(byUpdatedDesc)
      .map(visit),
  });
  return visit(root);
}

// Build the flat campaign list. Campaigns are the real manifests from
// `GET /campaigns`; cycles come from `/cycles`, split per campaign into its
// root (`rootCycleId(id) === id`) and the siblings that descend from it. A
// cycle whose campaign isn't in the campaign list is dropped — the registry
// is the source of truth for what's a campaign. A campaign whose root cycle
// dir isn't on disk yet can't be navigated, so it's dropped too rather than
// rendered as a headless branch list.
export function groupCampaigns(
  campaigns: CampaignSummary[],
  cycles: CycleListEntry[],
): CampaignGroup[] {
  const cyclesByCampaign = new Map<string, CycleListEntry[]>();
  for (const cyc of cycles) {
    const arr = cyclesByCampaign.get(cyc.campaign_id) ?? [];
    arr.push(cyc);
    cyclesByCampaign.set(cyc.campaign_id, arr);
  }

  const groups: CampaignGroup[] = [];
  for (const campaign of campaigns) {
    const own = cyclesByCampaign.get(campaign.campaign_id) ?? [];
    const root = own.find((cyc) => rootCycleId(cyc.cycle_id) === cyc.cycle_id);
    if (!root) continue;
    const branches = own
      .filter((cyc) => cyc.cycle_id !== root.cycle_id)
      .sort(byUpdatedDesc);
    const all = [root, ...branches];
    groups.push({
      campaign,
      root,
      branches,
      updatedAt: all.reduce(
        (m, c) => (c.updated_at > m ? c.updated_at : m),
        campaign.created_at,
      ),
      bestAccuracy: bestAccuracyOf(all),
    });
  }
  groups.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  return groups;
}

// We store the COLLAPSED set, not the expanded one — campaigns default to
// expanded, so empty storage = "show everything." Keys are prefixed (`cmp:`)
// so they never collide with another node kind.
export const COLLAPSED_STORAGE_KEY = "promptpotter.sidebar.collapsedNodes";
export const EMPTY_COLLAPSED: Set<string> = new Set();

// A Set doesn't JSON round-trip — persist it as a string array.
export const collapsedCodec = {
  serialize: (s: Set<string>) => JSON.stringify([...s]),
  deserialize: (raw: string): Set<string> => {
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed : []);
  },
};
