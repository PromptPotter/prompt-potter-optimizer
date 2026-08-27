// The `/tree` walk — THE reader of the served genealogy, peer of `round-candidates.ts`
// (which normalizes the live single-cycle `dashboard.json`). Two sources, one derivation
// each; a second copy of either is drift.
//
// The tree alternates `course → candidate → course` at every depth, so this is one
// recursion rather than a per-tier rule. `id` and `label` are minted facts on the node —
// never re-derived from a list position.

import type { LineageNode } from "@/lib/api";
import type { SelectedCandidate } from "@/lib/types";
import { encodeCyclePath, nodeAddress, type CyclePath } from "@/lib/ids";

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

// Everything one supersede cut left behind, and the branch that replaced it.
export interface RetiredGroup {
  branch: string;
  candidates: LineageNode[];
}

// The timeline split by which side of a supersede cut each candidate is on. Grouped by BRANCH:
// two cuts retire two different tails, and one row for both would say a single correction did
// it. Grouping only — `superseded_by` is served, and nothing is decided here.
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

// Every candidate whose OWN path is `path` — that course's timeline as THAT course minted
// it, addressed inside the one served tree.
//
// **The address is the `path`, read off the node it names** — never a label (`C1.1` is a
// course's private position, minted by every course) and never a bare cycle_id (inner ids
// repeat across sibling `.inner/` sandboxes; `path` is unique by construction).
//
// It answers for a FORK, which is the case a course lookup cannot reach: **a fork is not a
// node.** The server dissolves it onto the parent's timeline, so its contributed attempts —
// each carrying the fork's `path` — are the only trace of it in the tree.
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
// across sandboxes. It IS the sidebar's node address, so it is minted by the one builder
// (`lib/ids.ts`) that `ownerOfNodeAddress` reads back.
export function nodeKeyOf(node: LineageNode): string {
  return nodeAddress(pathOf(node), node.id);
}

// A served node as the app's SELECTION — the one mint, because the `label` field is the trap.
// `SelectedCandidate.label` is consumed as a JOIN KEY (`candidateObserveConfig`, the steer
// panel's seed, the samples groups), never as display text, so it carries the MINTING course's
// label: an attempt a fork contributed is renumbered onto the timeline it is drawn on while its
// round document still speaks the label its own course gave it, and an id join misses in silence.
// Three surfaces built this object by hand and one of them wrote `label` — the renumbered one.
//
// The CYCLE is the caller's: a node's own path names the course that minted it, while a drawing
// hands the lane it was placed on, and the two differ for exactly that fork-contributed attempt.
// The accuracy likewise, because a cladogram's node carries whichever metric it is inked with.
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

// ONE walk, indexed by encoded path — and THE lookup every surface uses. `candidatesAtPath`
// re-walks the whole tree per call; a surface that looks up per render (or holds several
// lookups) rides an index built once per tree instead. Semantics per entry:
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

// What a cladogram paints ON its nodes, keyed by the candidate's address. Deliberately apart
// from the geometry: a value tick repaints node text without re-flowing the tree.
//
// ONE SOURCE — the served tree. A second pass used to overwrite the in-view course's entries
// from `dashboard.json` "so the in-flight round tracks the poll", and it could never have done
// that: a live row's id is POSITIONAL (`r{round}_{idx}`) while every key here is `nodeKeyOf`.
// θ rides its own map because it is a logit, not a percent.
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
      // Accuracy view: every node paints what IT measured. The winner used to paint the
      // round's cumulative frontier — a pool of rows scored by different configurations — so
      // the spine read higher than anything the run had measured.
      valueByKey.set(key, composite ? cand.composite_fitness : (cand.accuracy ?? null));
      thetaByKey.set(key, cand.theta);
    }
  }
  return { valueByKey, thetaByKey };
}

// The label of the candidate a fork was CUT FROM — a badge, never the name. Null for
// anything this course minted itself.
export function cutFromLabel(node: LineageNode, siblings: readonly LineageNode[]): string | null {
  if (node.course_kind === null) return null;
  return siblings.find((s) => s.id === node.parent_id)?.label ?? null;
}
