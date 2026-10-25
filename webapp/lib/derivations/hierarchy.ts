import type { HierarchyCampaign, HierarchyCourse, HierarchyEntry, HierarchyTag } from "@/lib/api";
import { decodeCyclePath } from "@/lib/ids";
import { dockPriority } from "@/lib/run-phase";

export interface RootGroup {
  rootCycleId: string;
  campaigns: HierarchyCampaign[];
}

// The server serves `entries` root-major, so a group is a run of consecutive campaigns.
export function groupByRoot(entries: readonly HierarchyCampaign[]): RootGroup[] {
  const groups: RootGroup[] = [];
  for (const campaign of entries) {
    const rootCycleId = decodeCyclePath(campaign.facts.root)?.at(-1)?.cycleId;
    if (rootCycleId === undefined) throw new Error(`undecodable root ${campaign.facts.root}`);
    const last = groups.at(-1);
    if (last?.rootCycleId === rootCycleId) last.campaigns.push(campaign);
    else groups.push({ rootCycleId, campaigns: [campaign] });
  }
  return groups;
}

export function campaignAt(
  byAddress: ReadonlyMap<string, HierarchyEntry>,
  address: string,
): HierarchyCampaign | null {
  const entry = byAddress.get(address);
  return entry?.level === "campaign" ? entry : null;
}

export function coursesOf(campaign: HierarchyCampaign): HierarchyCourse[] {
  return campaign.children.filter((c): c is HierarchyCourse => c.level === "course");
}

export function runningCourses(entries: readonly HierarchyCampaign[]): HierarchyCourse[] {
  return entries
    .flatMap(coursesOf)
    .filter((c) => c.state.producer.attached)
    .sort((a, b) => dockPriority(a.state.run_phase) - dockPriority(b.state.run_phase));
}

interface Tagged {
  tags: readonly HierarchyTag[];
}

export function hasTag(entry: Tagged | null, kind: HierarchyTag["kind"]): boolean {
  return entry?.tags.some((t) => t.kind === kind) ?? false;
}

export function tagValue(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.value ?? null;
}

export function tagLabel(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.label ?? null;
}
