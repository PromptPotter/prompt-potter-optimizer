import type {
  HierarchyCampaign,
  HierarchyCandidate,
  HierarchyCourse,
  HierarchyEntry,
  LineageNode,
} from "@/lib/api";
import { selectedCandidateOf, type SelectedCandidate } from "@/lib/types";
import { tagValue } from "@/lib/hierarchy";
import { courseAddressOf, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { metricLevel, type DisplayMetric } from "./headline-stats";
import { coursesOf } from "./hierarchy";

export interface RetiredGroup {
  // The address of the course that took the tail's place.
  branch: string;
  candidates: HierarchyCandidate[];
}

// Grouped by BRANCH: two cuts retire two different tails.
export function splitRetired<N extends LineageNode>(
  rows: readonly N[],
): { live: N[]; retired: RetiredGroup[] } {
  const byBranch = new Map<string, HierarchyCandidate[]>();
  const live: N[] = [];
  for (const node of rows) {
    const branch = node.level === "candidate" ? tagValue(node, "superseded") : null;
    if (node.level === "candidate" && branch) {
      byBranch.set(branch, [...(byBranch.get(branch) ?? []), node]);
    } else live.push(node);
  }
  return { live, retired: [...byBranch].map(([branch, candidates]) => ({ branch, candidates })) };
}

// A course's own candidates, round by round in slot order; a candidate's inner courses.
export function childNodes(node: LineageNode): LineageNode[] {
  if (node.level === "course") {
    return node.children.flatMap((rnd) =>
      rnd.children.filter((c): c is HierarchyCandidate => c.level === "candidate"),
    );
  }
  return node.children.flatMap((c) => (c.level === "campaign" ? coursesOf(c) : []));
}

export function selectedNodeOf(node: HierarchyCandidate, cycleId: string): SelectedCandidate {
  const { round, key, label } = node.facts.reading.arm;
  return selectedCandidateOf(cycleId, round, key, label);
}

export interface LineageIndex {
  byAddress: ReadonlyMap<string, HierarchyEntry>;
  // Every course at any depth, a campaign's root first.
  courses: readonly HierarchyCourse[];
  candidates: readonly HierarchyCandidate[];
}

export const EMPTY_LINEAGE: LineageIndex = { byAddress: new Map(), courses: [], candidates: [] };

export function indexLineage(root: HierarchyCampaign | null): LineageIndex {
  if (!root) return EMPTY_LINEAGE;
  const byAddress = new Map<string, HierarchyEntry>();
  const courses: HierarchyCourse[] = [];
  const candidates: HierarchyCandidate[] = [];
  const visit = (entry: HierarchyEntry): void => {
    byAddress.set(entry.address, entry);
    if (entry.level === "course") courses.push(entry);
    if (entry.level === "candidate") candidates.push(entry);
    entry.children.forEach(visit);
  };
  visit(root);
  return { byAddress, courses, candidates };
}

export function courseAt(index: LineageIndex, path: CyclePath | null): HierarchyCourse | null {
  const entry = path ? index.byAddress.get(encodeCyclePath(path)) : undefined;
  return entry?.level === "course" ? entry : null;
}

export function courseOf(index: LineageIndex, node: LineageNode): HierarchyCourse | null {
  if (node.level === "course") return node;
  const entry = index.byAddress.get(courseAddressOf(node.address));
  return entry?.level === "course" ? entry : null;
}

export function candidateAt(index: LineageIndex, address: string | null): HierarchyCandidate | null {
  const entry = address ? index.byAddress.get(address) : undefined;
  return entry?.level === "candidate" ? entry : null;
}

export function candidatesAtPath(index: LineageIndex, path: CyclePath | null): HierarchyCandidate[] {
  const course = courseAt(index, path);
  return course ? (childNodes(course) as HierarchyCandidate[]) : [];
}

export function armOfId(index: LineageIndex, id: string): HierarchyCandidate | null {
  return index.candidates.find((c) => c.facts.individual === id && c.facts.answers_for_id) ?? null;
}

export function armAt(
  index: LineageIndex,
  path: CyclePath | null,
  key: string,
): HierarchyCandidate | null {
  return candidatesAtPath(index, path).find((c) => c.facts.reading.arm.key === key) ?? null;
}

// The candidate answering for the arm's individual; null for a refused proposal, which measured none.
export function individualOf(
  index: LineageIndex,
  node: HierarchyCandidate,
): HierarchyCandidate | null {
  return node.facts.reading.end === "invalid" ? null : armOfId(index, node.facts.individual);
}

export type LineStep = { kind: "step"; node: HierarchyCandidate } | { kind: "held"; round: number };

export function mainLineOf(index: LineageIndex, head: HierarchyCandidate): LineStep[] {
  return head.facts.main_line.flatMap<LineStep>((step) => {
    if (step.candidates.length === 0) return [{ kind: "held", round: step.round }];
    return step.candidates.flatMap<LineStep>((address) => {
      const node = candidateAt(index, address);
      return node ? [{ kind: "step", node }] : [];
    });
  });
}

// As the course names it (`facts.origin`): never a walk up `parent_ids`, never a round-0 label.
export function originOf(index: LineageIndex, node: LineageNode | null): HierarchyCandidate | null {
  const course = node ? courseOf(index, node) : null;
  return candidateAt(index, course?.facts.origin ?? null);
}

export function originAt(index: LineageIndex, path: CyclePath | null): HierarchyCandidate | null {
  return originOf(index, courseAt(index, path));
}

export interface NodeOverlays {
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
}

export function nodeOverlays(index: LineageIndex, metric: DisplayMetric): NodeOverlays {
  const valueByKey = new Map<string, number | null>();
  const thetaByKey = new Map<string, number | null>();
  for (const arm of index.candidates) {
    valueByKey.set(arm.address, metricLevel(metric, arm.facts.reading));
    thetaByKey.set(arm.address, metricLevel("ability", arm.facts.reading));
  }
  return { valueByKey, thetaByKey };
}
