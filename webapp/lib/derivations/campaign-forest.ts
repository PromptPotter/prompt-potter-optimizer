import type { CampaignSummary, CycleListEntry, LineStanding } from "@/lib/api";

export interface RunGroup {
  campaign: CampaignSummary;
  line: LineStanding;
  branches: CycleListEntry[];
}

export interface OriginGroup {
  originId: string;
  runs: RunGroup[];
}

export function buildForest(
  campaigns: CampaignSummary[],
  cycles: CycleListEntry[],
): OriginGroup[] {
  const branchesOf = new Map<string, CycleListEntry[]>();
  for (const cyc of cycles) {
    if (cyc.is_root) continue;
    const arr = branchesOf.get(cyc.campaign_id) ?? [];
    arr.push(cyc);
    branchesOf.set(cyc.campaign_id, arr);
  }

  const byOrigin = new Map<string, RunGroup[]>();
  for (const campaign of campaigns) {
    if (campaign.line === null) continue;
    const run = {
      campaign,
      line: campaign.line,
      branches: branchesOf.get(campaign.campaign_id) ?? [],
    };
    const arr = byOrigin.get(campaign.root_cycle_id) ?? [];
    arr.push(run);
    byOrigin.set(campaign.root_cycle_id, arr);
  }
  return [...byOrigin].map(([originId, runs]) => ({ originId, runs }));
}

export function filterForest(
  forest: OriginGroup[],
  keep: (campaign: CampaignSummary) => boolean,
): OriginGroup[] {
  const origins: OriginGroup[] = [];
  for (const origin of forest) {
    const runs = origin.runs.filter((r) => keep(r.campaign));
    if (runs.length > 0) origins.push({ originId: origin.originId, runs });
  }
  return origins;
}

// `kind` separates tiers that share an address (a declaration and its sole run).
export type NodeKind = "org" | "course" | "cand" | "retired";

export function nodeKey(kind: NodeKind, path: string): string {
  return `${kind}:${path}`;
}

// A `course` fetches `/tree` on open, so it stays closed; `retired` repeats the live timeline's labels.
const OPEN_BY_DEFAULT: Record<NodeKind, boolean> = {
  org: true,
  course: false,
  cand: true,
  retired: false,
};

export function isNodeOpen(toggled: ReadonlySet<string>, kind: NodeKind, path: string): boolean {
  return toggled.has(nodeKey(kind, path)) ? !OPEN_BY_DEFAULT[kind] : OPEN_BY_DEFAULT[kind];
}
