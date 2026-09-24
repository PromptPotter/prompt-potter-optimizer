// The one reader of the served `/tree` genealogy (`round-candidates.ts` owns `dashboard.json`).
// `id` and `label` are minted on the node — never re-derived from a list position.

import type { LineageNode } from "@/lib/api";
import type { SelectedCandidate } from "@/lib/types";
import { encodeCyclePath, nodeAddress, type CyclePath } from "@/lib/ids";

// The one served-path → CyclePath conversion; no surface re-maps one by hand.
export function pathOf(node: LineageNode): CyclePath {
  return node.path.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id }));
}

export function candidatesOf(course: LineageNode | undefined): LineageNode[] {
  return (course?.children ?? []).filter((c) => c.kind === "candidate");
}

export interface RetiredGroup {
  branch: string;
  candidates: LineageNode[];
}

// Grouped by BRANCH: two cuts retire two different tails, and one row for both would say a
// single correction did it.
export function splitRetired(rows: readonly LineageNode[]): {
  live: LineageNode[];
  retired: RetiredGroup[];
} {
  const byBranch = new Map<string, LineageNode[]>();
  const live: LineageNode[] = [];
  for (const cand of rows) {
    if (!cand.superseded_by) live.push(cand);
    else byBranch.set(cand.superseded_by, [...(byBranch.get(cand.superseded_by) ?? []), cand]);
  }
  return { live, retired: [...byBranch].map(([branch, candidates]) => ({ branch, candidates })) };
}

// A fork is never one of these: it is not a node, its candidates sit on the parent's timeline.
export function childCourses(candidate: LineageNode | undefined): LineageNode[] {
  return (candidate?.children ?? []).filter((c) => c.kind === "course");
}

export function walkCourses(root: LineageNode): LineageNode[] {
  const out: LineageNode[] = [];
  const visit = (node: LineageNode): void => {
    if (node.kind === "course") out.push(node);
    for (const child of node.children) visit(child);
  };
  visit(root);
  return out;
}

export function countDescendants(root: LineageNode): number {
  return walkCourses(root).length - 1;
}

// Keyed on `path`, never a label or bare cycle_id (both repeat across courses and `.inner/`
// sandboxes). A fork is not a node, so its attempts carrying its path are its only trace.
export function candidatesAtPath(root: LineageNode, path: CyclePath): LineageNode[] {
  const want = encodeCyclePath(path);
  const out: LineageNode[] = [];
  const visit = (node: LineageNode): void => {
    if (node.kind === "candidate" && encodeCyclePath(pathOf(node)) === want) out.push(node);
    for (const child of node.children) visit(child);
  };
  visit(root);
  return out;
}

// Unique tree-wide (course ids collide across sandboxes); it IS the sidebar's node address,
// which `ownerOfNodeAddress` reads back.
export function nodeKeyOf(node: LineageNode): string {
  return nodeAddress(pathOf(node), node.id);
}

// The one selection mint. `label` is a downstream JOIN KEY, so it carries the MINTING course's
// label — a fork-contributed attempt's round document speaks that one, not the renumbered one.
export function selectedCandidateOf(
  node: LineageNode,
  cycleId: string,
  accuracy: number | null = node.accuracy,
): SelectedCandidate {
  return {
    cycle_id: cycleId,
    round: node.round ?? 0,
    candidate_id: node.id,
    label: node.course_label,
    accuracy,
    is_winner: node.is_winner,
  };
}

// `course` is null at a fork's address (a fork is not a node); `candidates` follows
// `candidatesAtPath`.
export interface LineageAddress {
  course: LineageNode | null;
  candidates: LineageNode[];
}
export type LineageIndex = ReadonlyMap<string, LineageAddress>;

export function indexLineage(root: LineageNode | null): LineageIndex {
  const index = new Map<string, LineageAddress>();
  if (!root) return index;
  const at = (key: string): LineageAddress => {
    let entry = index.get(key);
    if (!entry) {
      entry = { course: null, candidates: [] };
      index.set(key, entry);
    }
    return entry;
  };
  const visit = (node: LineageNode): void => {
    const key = encodeCyclePath(pathOf(node));
    if (node.kind === "course") at(key).course = node;
    else if (node.kind === "candidate") at(key).candidates.push(node);
    for (const child of node.children) visit(child);
  };
  visit(root);
  return index;
}

// Served tree only: a `dashboard.json` row id is positional (`r{round}_{idx}`) and never
// matches a `nodeKeyOf` key. θ rides its own map because it is a logit, not a percent.
export interface NodeOverlays {
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
}

export function nodeOverlays(
  courses: readonly LineageNode[],
  composite: boolean,
): NodeOverlays {
  const valueByKey = new Map<string, number | null>();
  const thetaByKey = new Map<string, number | null>();
  for (const course of courses) {
    for (const cand of candidatesOf(course)) {
      const key = nodeKeyOf(cand);
      // Every node paints what IT measured, never the round's cumulative frontier.
      valueByKey.set(key, composite ? cand.composite_fitness : (cand.accuracy ?? null));
      thetaByKey.set(key, cand.theta);
    }
  }
  return { valueByKey, thetaByKey };
}

// A badge, never the name.
export function cutFromLabel(node: LineageNode, siblings: readonly LineageNode[]): string | null {
  if (node.course_kind === null) return null;
  return siblings.find((s) => s.id === node.parent_id)?.label ?? null;
}
