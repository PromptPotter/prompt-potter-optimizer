// The `/tree` walk — THE reader of the served genealogy, peer of `round-candidates.ts`
// (which normalizes the live single-cycle `dashboard.json`). Two sources, one derivation
// each; a second copy of either is drift.
//
// The tree alternates `course → candidate → course` at every depth, so this is one
// recursion rather than a per-tier rule. `id` and `label` are minted facts on the node —
// never re-derived from a list position.

import type { LineageNode } from "@/lib/api";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

// A node's address. The served hops are the wire's snake_case; `CyclePath` is the app's.
// ONE conversion, here, so no surface re-maps a served path into an address by hand.
export function pathOf(node: LineageNode): CyclePath {
  return node.path.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id }));
}

// A node's children, split by what they are. A course's children are its timeline; a
// candidate's are the courses that measured it. Nothing else is in either list.
export function candidatesOf(course: LineageNode | undefined): LineageNode[] {
  return (course?.children ?? []).filter((c) => c.kind === "candidate");
}

// The courses hanging off a candidate — an L4 inner run filed under it. A fork is NOT one
// of these: a fork is not a node, its candidates sit on the parent's timeline.
export function childCourses(candidate: LineageNode | undefined): LineageNode[] {
  return (candidate?.children ?? []).filter((c) => c.kind === "course");
}

// Every course in the tree, root first.
export function walkCourses(root: LineageNode): LineageNode[] {
  const out: LineageNode[] = [];
  const visit = (node: LineageNode): void => {
    if (node.kind === "course") out.push(node);
    for (const child of node.children) visit(child);
  };
  visit(root);
  return out;
}

// Courses below this one — the "+N more" count on a collapsed root. `walkCourses`
// includes the root itself; this counts what hangs BELOW it.
export function countDescendants(root: LineageNode): number {
  return walkCourses(root).length - 1;
}

// THE lookup: the node at an address.
//
// **The address is `(path, candidateId)`, read off the node it names** — never a label
// (`C1.1` is a course's private position, minted by every course) and never a bare cycle_id
// (inner ids repeat across sibling `.inner/` sandboxes; `path` is unique by construction).
//
// `candidateId` null addresses the course itself. A fork's path with no candidate addresses
// nothing, correctly: there is no fork node to view.
export function nodeAt(
  root: LineageNode,
  path: CyclePath,
  candidateId?: string | null,
): LineageNode | undefined {
  const want = encodeCyclePath(path);
  const wantKind = candidateId ? "candidate" : "course";
  const visit = (node: LineageNode): LineageNode | undefined => {
    if (
      node.kind === wantKind &&
      encodeCyclePath(pathOf(node)) === want &&
      (!candidateId || node.id === candidateId)
    ) {
      return node;
    }
    for (const child of node.children) {
      const hit = visit(child);
      if (hit) return hit;
    }
    return undefined;
  };
  return visit(root);
}

// Every candidate whose OWN path is `path` — that course's timeline as THAT course minted
// it, addressed inside the one served tree.
//
// This exists because `nodeAt(root, forkPath)` cannot answer for a fork: **a fork is not a
// node.** The server dissolves it onto the parent's timeline, so its contributed attempts —
// each carrying the fork's `path` — are the only trace of it in the tree, and a course
// lookup finds nothing. Scanning for the fork's cycle_id would be the bug this file's header
// warns about; the path is the address.
//
// For an ordinary course this returns its own candidates MINUS anything a fork contributed
// to it, which is what a per-course reader wants: a fork's attempts belong to the fork's own
// timeline, not to the course that renumbered them.
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

// A node's key, unique across the whole tree — unlike its `id`, since course ids collide
// across sandboxes.
export function nodeKeyOf(node: LineageNode): string {
  return `${encodeCyclePath(pathOf(node))}|${node.id}`;
}

// ONE walk, indexed by encoded path. `nodeAt` / `candidatesAtPath` each re-walk the whole
// tree per call; a surface that looks up per render (or holds several lookups) rides an
// index built once per tree instead. Semantics per entry:
//   `course`     — the course node AT that address (null for a fork: a fork is not a node).
//   `candidates` — the candidates whose OWN path it is (`candidatesAtPath` semantics: a
//                  fork's attempts under the fork's address, a course's own timeline minus
//                  fork contributions under its).
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

// The label of the candidate a fork was CUT FROM — a badge, never the name. Null for
// anything this course minted itself.
export function cutFromLabel(node: LineageNode, siblings: readonly LineageNode[]): string | null {
  if (node.course_kind === null) return null;
  return siblings.find((s) => s.id === node.parent_id)?.label ?? null;
}
