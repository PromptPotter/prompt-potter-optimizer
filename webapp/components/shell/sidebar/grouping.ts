// Sidebar data model — the FOREST, and the collapsed-node codec. Pure: no
// React; the tree-row components own the rendering.
//
// One store is one forest, and the structure is self-similar:
//
//   Forest → Origin → Run → Cycle-tree → (Inner Forest)
//
//   Origin  ≡ `root_cycle_id` (= `cycle_<root_content_hash>`) — the declaration
//             the loop starts from. Campaigns on one declaration SHARE it.
//   Run     ≡ one campaign (`{dataset}__{rand6}`). Two runs under one origin are
//             two measurements of the same spec — at L4 that means two candidates
//             produced an identical optimizer prompt (mode collapse), which is why the
//             origin tier is worth seeing rather than a flat run list.
//   Cycle   ≡ a run's root cycle plus the forks/diags/sweeps descending from it.
//   A cycle can open its own inner forest (`.inner/<cycle_id>`) — the recursion
//   closes, and L5 is the same loop one turn deeper. Nothing here is depth-aware.

import type { CampaignSummary, CycleListEntry } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// One run: the campaign manifest, its single root cycle, and every fork / diag /
// sweep descending from it. A campaign mints exactly one root (`{dataset}__{rand6}`
// per `new`), so there is no session tier.
//
// The ⋯ menu is campaign-scoped, so `root` is named apart from its forks — not
// because one contains the other. As candidate-groups they are peers: each holds its
// own origin and candidates, and a fork is a sibling course spawned from a candidate.
export interface RunGroup {
  campaign: CampaignSummary;
  root: CycleListEntry;
  // Most-recent updated_at across every cycle — sorts so the run being actively
  // worked on stays at the top.
  updatedAt: string;
  // Best fitness across the whole fork-tree (root + branches) — the winner often
  // lives in a fork, so this is NOT just the root's number.
  bestAccuracy: number | null;
}

// One origin and the runs measuring it. `originId` is the shared `root_cycle_id`.
export interface OriginGroup {
  originId: string;
  runs: RunGroup[];
  updatedAt: string;
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

const byUpdatedDesc = (a: CycleListEntry, b: CycleListEntry) =>
  a.updated_at < b.updated_at ? 1 : -1;

// Build one store's runs. Campaigns are the real manifests from `GET /campaigns`;
// cycles come from `/cycles`, split per campaign into its root
// (`rootCycleId(id) === id`) and the siblings descending from it. A cycle whose
// campaign isn't in the campaign list is dropped — the registry is the source of
// truth for what's a campaign. A campaign whose root cycle dir isn't on disk yet
// can't be navigated, so it's dropped too rather than rendered as a headless
// branch list.
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
    const root = own.find((cyc) => rootCycleId(cyc.cycle_id) === cyc.cycle_id);
    if (!root) continue;
    const branches = own
      .filter((cyc) => cyc.cycle_id !== root.cycle_id)
      .sort(byUpdatedDesc);
    const all = [root, ...branches];
    runs.push({
      campaign,
      root,
      updatedAt: all.reduce(
        (m, c) => (c.updated_at > m ? c.updated_at : m),
        campaign.created_at,
      ),
      bestAccuracy: bestAccuracyOf(all),
    });
  }
  return runs;
}

// Build a forest — the ONE builder, used at every depth (top level, an L4 cycle's
// inner fan-out, L5+). Runs are grouped by their campaign's `root_cycle_id`, which
// IS the origin identity: `cycle_<root_content_hash>`, shared by every campaign on
// the same declaration.
export function buildForest(
  campaigns: CampaignSummary[],
  cycles: CycleListEntry[],
): OriginGroup[] {
  const byOrigin = new Map<string, RunGroup[]>();
  for (const run of groupRuns(campaigns, cycles)) {
    // Prefer the manifest's `root_cycle_id`; fall back to deriving it off the
    // root cycle so a manifest written before the field existed still groups.
    const originId = run.campaign.root_cycle_id || rootCycleId(run.root.cycle_id);
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

// A node's stable key. `kind` separates the tiers that can share an address — a
// declaration and its sole run — and `path` is the CyclePath-encoded address, so
// keys are unique at any depth.
export type NodeKind = "org" | "course" | "cand";

export function nodeKey(kind: NodeKind, path: string): string {
  return `${kind}:${path}`;
}

// Defaults per tier, decided by what OPENING one costs.
//
// `org` is free — the structure is already in hand from the forest's two reads — so
// it comes open.
//
// `course` costs a fetch, so it comes closed and opens on ask: opening one pulls its
// candidates (`/tree`, one per campaign) and, at L4, its inner store. Every
// campaign wears a course row, so open-by-default would fire both for every row in
// the sidebar at load.
//
// `cand` opens free — the forks under it came with the campaign's cycle list, and the
// inner runs with the course that owns the sandbox — so a candidate shows what came
// of it without a second click. It cannot cascade: anything one hop further is a
// `course`, closed, so the next fetch waits for the next ask.
const OPEN_BY_DEFAULT: Record<NodeKind, boolean> = {
  org: true,
  course: false,
  cand: true,
};

export function isNodeOpen(toggled: ReadonlySet<string>, kind: NodeKind, path: string): boolean {
  return toggled.has(nodeKey(kind, path)) ? !OPEN_BY_DEFAULT[kind] : OPEN_BY_DEFAULT[kind];
}

// The toggled set is every node TOGGLED AWAY FROM ITS DEFAULT — so an empty set means
// "every node as it comes," and one set covers all node kinds. Persistence is PER CAMPAIGN
// (`lib/view-memory.tsx`); the keys were always campaign-partitioned by their address, so
// the one global blob that used to hold them was sharing for no reason.
