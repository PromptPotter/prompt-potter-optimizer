import type {
  HierarchyCampaign,
  HierarchyCourse,
  HierarchyEntry,
  HierarchyTag,
  ListedCampaign,
} from "@/lib/api";
import type { Figure } from "@/lib/api/types.generated";

export type FigureKey = Figure["key"];
import { decodeCyclePath } from "@/lib/ids";
import { dockPriority } from "./run-phase";

export interface RootGroup {
  rootCycleId: string;
  campaigns: ListedCampaign[];
}

// The server serves `entries` root-major, so a group is a run of consecutive campaigns. An
// unstarted campaign has no root cycle: it stands alone, keyed by its own address.
export function groupByRoot(entries: readonly ListedCampaign[]): RootGroup[] {
  const groups: RootGroup[] = [];
  for (const campaign of entries) {
    if (campaign.level === "unstarted") {
      groups.push({ rootCycleId: campaign.address, campaigns: [campaign] });
      continue;
    }
    const { root } = campaign.facts;
    const rootCycleId = decodeCyclePath(root)?.at(-1)?.cycleId;
    if (rootCycleId === undefined) throw new Error(`undecodable root ${root}`);
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

export function runningCourses(entries: readonly HierarchyCampaign[]): HierarchyCourse[] {
  return entries
    .flatMap((campaign) => campaign.children)
    .filter((c) => c.state.attached)
    .sort(
      (a, b) => dockPriority(a.state.status.run_phase) - dockPriority(b.state.status.run_phase),
    );
}

// An entry without the entries under it: what a frozen item or a copy holds of one row.
export function entryAlone<E extends object>(entry: E): Omit<E, "children"> {
  return Object.fromEntries(Object.entries(entry).filter(([k]) => k !== "children")) as Omit<
    E,
    "children"
  >;
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

/** An entry's served figure of *key*; null where it serves none. */
export function figureOf(entry: { figures: readonly Figure[] } | null, key: FigureKey): Figure | null {
  return entry?.figures.find((row) => row.key === key) ?? null;
}

export function tagLabel(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.label ?? null;
}
