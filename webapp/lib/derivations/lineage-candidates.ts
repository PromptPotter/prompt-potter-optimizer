import type { ArmNode, ArmPointer, CourseNode, LineageNode } from "@/lib/api";
import { selectedCandidateOf, type SelectedCandidate } from "@/lib/types";
import { encodeCyclePath, nodeAddress, type CyclePath } from "@/lib/ids";
import { metricLevel, type DisplayMetric } from "./headline-stats";

export function pathOf(node: LineageNode): CyclePath {
  return node.path.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id }));
}

export interface RetiredGroup {
  branch: string;
  candidates: ArmNode[];
}

// Grouped by BRANCH: two cuts retire two different tails.
export function splitRetired<N extends LineageNode>(
  rows: readonly N[],
): { live: N[]; retired: RetiredGroup[] } {
  const byBranch = new Map<string, ArmNode[]>();
  const live: N[] = [];
  for (const node of rows) {
    if (node.kind === "candidate" && node.superseded_by) {
      byBranch.set(node.superseded_by, [...(byBranch.get(node.superseded_by) ?? []), node]);
    } else live.push(node);
  }
  return { live, retired: [...byBranch].map(([branch, candidates]) => ({ branch, candidates })) };
}

export function walkCourses(root: CourseNode): CourseNode[] {
  const out: CourseNode[] = [];
  const visit = (course: CourseNode): void => {
    out.push(course);
    for (const arm of course.children) arm.children.forEach(visit);
  };
  visit(root);
  return out;
}

export function countDescendants(root: CourseNode): number {
  return walkCourses(root).length - 1;
}

// Unique tree-wide (course ids collide across sandboxes); `ownerOfNodeAddress` reads it back.
export function nodeKeyOf(node: LineageNode): string {
  return nodeAddress(pathOf(node), node.id);
}

export function selectedNodeOf(node: LineageNode, cycleId: string): SelectedCandidate {
  return node.kind === "candidate"
    ? selectedCandidateOf(cycleId, node.reading.arm.round, node.id, node.reading.arm.label)
    : selectedCandidateOf(cycleId, 0, node.id, node.label);
}

// `course` is null at a fork's address: a fork is not a node, and its arms are its only trace.
export interface LineageAddress {
  course: CourseNode | null;
  candidates: ArmNode[];
}
export type LineageIndex = ReadonlyMap<string, LineageAddress>;

// Keyed on `path`: a label or bare cycle_id repeats across courses and `.inner/` sandboxes.
export function indexLineage(root: CourseNode | null): LineageIndex {
  const index = new Map<string, LineageAddress>();
  if (!root) return index;
  const at = (node: LineageNode): LineageAddress => {
    const key = encodeCyclePath(pathOf(node));
    let entry = index.get(key);
    if (!entry) {
      entry = { course: null, candidates: [] };
      index.set(key, entry);
    }
    return entry;
  };
  const visit = (course: CourseNode): void => {
    at(course).course = course;
    for (const arm of course.children) {
      at(arm).candidates.push(arm);
      arm.children.forEach(visit);
    }
  };
  visit(root);
  return index;
}

export function candidatesAtPath(index: LineageIndex, path: CyclePath | null): ArmNode[] {
  return (path && index.get(encodeCyclePath(path))?.candidates) || [];
}

function findArm(index: LineageIndex, named: (arm: ArmNode) => boolean): ArmNode | null {
  for (const { candidates } of index.values()) {
    const arm = candidates.find(named);
    if (arm) return arm;
  }
  return null;
}

export function armOfRow(index: LineageIndex, row: number | null): ArmNode | null {
  return row === null ? null : findArm(index, (c) => c.row === row);
}

export function armOfId(index: LineageIndex, id: string): ArmNode | null {
  return findArm(index, (c) => c.id === id && c.answers_for_id);
}

export function armAt(
  index: LineageIndex,
  path: CyclePath | null,
  arm: Pick<ArmPointer, "round" | "label">,
): ArmNode | null {
  return (
    candidatesAtPath(index, path).find(
      (c) => c.reading.arm.round === arm.round && c.reading.arm.label === arm.label,
    ) ?? null
  );
}

export type LineStep = { kind: "step"; node: ArmNode } | { kind: "held"; round: number };

export function mainLineOf(index: LineageIndex, head: ArmNode): LineStep[] {
  return head.main_line.flatMap<LineStep>((step) => {
    if (step.rows.length === 0) return [{ kind: "held", round: step.round }];
    return step.rows.flatMap<LineStep>((row) => {
      const node = armOfRow(index, row);
      return node ? [{ kind: "step", node }] : [];
    });
  });
}

// As the tree names it (`origin_row`): never a walk up `parent_ids`, never a round-0 label.
export function originOf(index: LineageIndex, node: LineageNode | undefined): ArmNode | null {
  return node ? armOfRow(index, node.origin_row) : null;
}

// At a fork's address there is no course, so the attempts it contributed answer for it.
export function originAt(index: LineageIndex, path: CyclePath | null): ArmNode | null {
  const here = path ? index.get(encodeCyclePath(path)) : undefined;
  return originOf(index, here?.course ?? here?.candidates[0]);
}

export interface NodeOverlays {
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
}

export function nodeOverlays(courses: readonly CourseNode[], metric: DisplayMetric): NodeOverlays {
  const valueByKey = new Map<string, number | null>();
  const thetaByKey = new Map<string, number | null>();
  for (const course of courses) {
    for (const arm of course.children) {
      const key = nodeKeyOf(arm);
      valueByKey.set(key, metricLevel(metric, arm.reading));
      thetaByKey.set(key, metricLevel("ability", arm.reading));
    }
  }
  return { valueByKey, thetaByKey };
}