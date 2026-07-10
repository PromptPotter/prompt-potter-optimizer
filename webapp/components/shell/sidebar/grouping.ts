// Sidebar data model: the campaign → session → fork-tree grouping logic and
// the collapsed-node persistence codec. Pure — no React; the Sidebar tree-row
// components own the rendering.

import type { CampaignSummary, CycleListEntry } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// One session in a campaign's forest: its root cycle + every fork / diag /
// sweep that descends from it.
export interface SessionGroup {
  root: CycleListEntry;
  branches: CycleListEntry[];
  // Most-recent updated_at across the session's units.
  updatedAt: string;
  // Best fitness across the session's whole fork-tree (root + branches) — the
  // winner often lives in a fork, so this is NOT just the root's number.
  bestAccuracy: number | null;
}

// One campaign's row in the tree: the manifest + its N sessions.
export interface CampaignGroup {
  campaign: CampaignSummary;
  sessions: SessionGroup[];
  // Most-recent updated_at across every session — sorts campaigns so the
  // one being actively worked on stays at the top.
  updatedAt: string;
  // Best fitness across every cycle in the campaign forest.
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

// Same null-aware max, over already-derived session bests.
function maxNullable(values: (number | null)[]): number | null {
  let best: number | null = null;
  for (const v of values) {
    if (v != null && (best == null || v > best)) best = v;
  }
  return best;
}

// One node in a session's fork-tree: a unit plus the units forked off it.
export interface UnitNode {
  unit: CycleListEntry;
  children: UnitNode[];
}

const byUpdatedDesc = (a: CycleListEntry, b: CycleListEntry) =>
  a.updated_at < b.updated_at ? 1 : -1;

// Build a session's fork-tree from `parent_cycle_id`. The root is the
// trunk; every branch nests under its parent. A branch whose parent isn't
// in the session attaches to the root so it can never vanish.
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
// `GET /campaigns`; cycles come from `/cycles`, partitioned per campaign
// into sessions by their family root (`rootCycleId`). A cycle whose
// campaign isn't in the campaign list is dropped — the registry is the
// source of truth for what's a campaign.
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
    // Partition the campaign's cycles into sessions by family root.
    const bySession = new Map<
      string,
      { root: CycleListEntry | null; branches: CycleListEntry[] }
    >();
    for (const cyc of own) {
      const sr = rootCycleId(cyc.cycle_id);
      let s = bySession.get(sr);
      if (!s) {
        s = { root: null, branches: [] };
        bySession.set(sr, s);
      }
      if (cyc.cycle_id === sr) s.root = cyc;
      else s.branches.push(cyc);
    }
    const sessions: SessionGroup[] = [];
    for (const s of bySession.values()) {
      // A session with no root cycle dir on disk can't be navigated —
      // skip it rather than render a headless branch list.
      if (!s.root) continue;
      s.branches.sort(byUpdatedDesc);
      const updatedAt = [s.root, ...s.branches].reduce(
        (m, c) => (c.updated_at > m ? c.updated_at : m),
        s.root.updated_at,
      );
      sessions.push({
        root: s.root,
        branches: s.branches,
        updatedAt,
        bestAccuracy: bestAccuracyOf([s.root, ...s.branches]),
      });
    }
    sessions.sort((a, b) => (a.root.cycle_id < b.root.cycle_id ? -1 : 1));
    const updatedAt = sessions.reduce(
      (m, s) => (s.updatedAt > m ? s.updatedAt : m),
      campaign.created_at,
    );
    groups.push({
      campaign,
      sessions,
      updatedAt,
      bestAccuracy: maxNullable(sessions.map((s) => s.bestAccuracy)),
    });
  }
  groups.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  return groups;
}

// We store the COLLAPSED set, not the expanded one — campaigns + sessions
// default to expanded, so empty storage = "show everything." Keys are
// prefixed (`cmp:` / `sess:`) so campaign and session ids never collide.
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

export const sessKey = (campaignId: string, rootId: string) =>
  `sess:${campaignId}::${rootId}`;
