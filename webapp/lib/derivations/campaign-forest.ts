// The campaign forest (Forest → Origin → Run → cycles → inner forest) and the collapsed-node
// codec. Origin = the shared `root_cycle_id`; nothing here is depth-aware.

import type { CampaignSummary, CycleListEntry } from "@/lib/api";

export interface RunGroup {
  campaign: CampaignSummary;
  root: CycleListEntry;
  // After a cut the line continues on a FORK, so this is usually the root but never assumed to be.
  answering: CycleListEntry;
  branches: CycleListEntry[];
  updatedAt: string;
  // Across the whole fork-tree: the winner often lives in a fork.
  bestAccuracy: number | null;
}

export interface OriginGroup {
  originId: string;
  runs: RunGroup[];
  updatedAt: string;
  bestAccuracy: number | null;
}

// A selection, not a computation: an origin is no server entity, so nothing could serve its max.
function bestAccuracyOf(entries: CycleListEntry[]): number | null {
  let best: number | null = null;
  for (const e of entries) {
    if (e.best_accuracy != null && (best == null || e.best_accuracy > best)) {
      best = e.best_accuracy;
    }
  }
  return best;
}

const byUpdatedDesc = (a: CycleListEntry, b: CycleListEntry) =>
  a.updated_at < b.updated_at ? 1 : -1;

// A rebase chains, so follow `superseded_by`; `seen` bounds a cycle that would hang the sidebar.
function answeringCycle(root: CycleListEntry, branches: CycleListEntry[]): CycleListEntry {
  const byId = new Map(branches.map((b) => [b.cycle_id, b]));
  let cur = root;
  const seen = new Set<string>([cur.cycle_id]);
  while (cur.superseded_by && !seen.has(cur.superseded_by)) {
    const next = byId.get(cur.superseded_by);
    // A successor absent from the list (archived, filtered) leaves the parent answering.
    if (!next) break;
    seen.add(next.cycle_id);
    cur = next;
  }
  return cur;
}

// `GET /campaigns` decides what is a campaign; one whose root cycle is not on disk yet is dropped.
function groupRuns(
  campaigns: CampaignSummary[],
  cycles: CycleListEntry[],
): RunGroup[] {
  const cyclesByCampaign = new Map<string, CycleListEntry[]>();
  for (const cyc of cycles) {
    const arr = cyclesByCampaign.get(cyc.campaign_id) ?? [];
    arr.push(cyc);
    cyclesByCampaign.set(cyc.campaign_id, arr);
  }

  const runs: RunGroup[] = [];
  for (const campaign of campaigns) {
    const own = cyclesByCampaign.get(campaign.campaign_id) ?? [];
    const root = own.find((cyc) => cyc.is_root);
    if (!root) continue;
    const branches = own
      .filter((cyc) => cyc.cycle_id !== root.cycle_id)
      .sort(byUpdatedDesc);
    const all = [root, ...branches];
    runs.push({
      campaign,
      root,
      answering: answeringCycle(root, branches),
      branches,
      updatedAt: all.reduce(
        (m, c) => (c.updated_at > m ? c.updated_at : m),
        campaign.created_at,
      ),
      bestAccuracy: bestAccuracyOf(all),
    });
  }
  return runs;
}

// The one builder at every depth.
export function buildForest(
  campaigns: CampaignSummary[],
  cycles: CycleListEntry[],
): OriginGroup[] {
  const byOrigin = new Map<string, RunGroup[]>();
  for (const run of groupRuns(campaigns, cycles)) {
    const originId = run.campaign.root_cycle_id;
    const arr = byOrigin.get(originId) ?? [];
    arr.push(run);
    byOrigin.set(originId, arr);
  }

  const origins: OriginGroup[] = [];
  for (const [originId, runs] of byOrigin) {
    runs.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
    origins.push({
      originId,
      runs,
      updatedAt: runs.reduce((m, r) => (r.updatedAt > m ? r.updatedAt : m), ""),
      bestAccuracy: runs.reduce<number | null>(
        (b, r) =>
          r.bestAccuracy != null && (b == null || r.bestAccuracy > b)
            ? r.bestAccuracy
            : b,
        null,
      ),
    });
  }
  origins.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  return origins;
}

// `kind` separates tiers that share an address (a declaration and its sole run).
export type NodeKind = "org" | "course" | "cand" | "retired";

export function nodeKey(kind: NodeKind, path: string): string {
  return `${kind}:${path}`;
}

// Decided by what opening costs: a `course` fetches `/tree` (and at L4 its inner store), so it
// stays closed; `retired` closes because its labels repeat the live timeline's.
const OPEN_BY_DEFAULT: Record<NodeKind, boolean> = {
  org: true,
  course: false,
  cand: true,
  retired: false,
};

export function isNodeOpen(toggled: ReadonlySet<string>, kind: NodeKind, path: string): boolean {
  return toggled.has(nodeKey(kind, path)) ? !OPEN_BY_DEFAULT[kind] : OPEN_BY_DEFAULT[kind];
}
