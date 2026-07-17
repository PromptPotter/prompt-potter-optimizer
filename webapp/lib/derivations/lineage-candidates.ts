// The `/tree` walk — THE normalizer for the served genealogy, peer of
// `round-candidates.ts` (which normalizes the live single-cycle `dashboard.json`).
// Two sources, two derivations, one each — a second copy of either is the drift the
// candidate spine exists to prevent.
//
// The tree alternates `course → candidate → (course | sample)` at every depth, so
// everything here is one recursion rather than a per-tier rule. Identity is served:
// `candidate_id` and `label` are minted facts on the node, never re-derived from a
// list position — which is what the position fallbacks used to be papering over.

import type { LineageNode } from "@/lib/api";

export interface LineageCandidate {
  candidateId: string;
  // `C0` for the origin, `C{round}.{n}` otherwise — the SAME string the round file,
  // the console and the candidates chart use. Minted at L1/origin and served.
  label: string;
  round: number;
  accuracy: number | null;
  compositeFitness: number | null;
  isWinner: boolean;
  // Did this round elect a winner at all? A held round advances nobody, and a
  // consumer that infers "winner = first candidate" crowns an eliminated one.
  advanced: boolean;
  // Did this round CLOSE? It separates the two ways a round crowns nobody: closed
  // with no winner = held (it ran, nothing beat the incumbent); not closed = it
  // never finished, so there is no election to report. `advanced` alone collapses
  // those into one false — which is how a never-closed round's lone candidate got
  // promoted to a fake winner.
  roundClosed: boolean;
  // Did this round have RIVALS? Round 0 runs one candidate — the origin — so its
  // `is_winner` is true by having nobody to beat. That is a fact about the round's
  // shape, not an achievement, and badging C0 "won" beside the candidate that
  // actually won put two winners on one course.
  contested: boolean;
  // A FORK the operator cut, folded onto this course's timeline as the attempt it is.
  // `candidateId` is then the fork's own cycle_id, and `label` is its position on the
  // campaign's ONE timeline — the fork cut fourth reads `C1.4`. Served: a fork's label
  // cannot be minted (it is not an L1 candidate), and every course mints its own private
  // `C1.1`, so the per-course counter names nothing campaign-wide.
  isFork: boolean;
  // The label of the candidate this fork was CUT FROM (`C0`) — a badge, never the name.
  // Null for anything L1 minted.
  cutFrom: string | null;
}

// Every course in the tree, root first. A fork and an L4 inner run are the same
// edge here (both are courses hanging off a candidate), so one walk reaches every
// depth and no caller branches on which kind it found.
export function walkCourses(root: LineageNode): LineageNode[] {
  const out: LineageNode[] = [];
  const visit = (node: LineageNode): void => {
    if (node.kind === "course") out.push(node);
    for (const child of node.children) visit(child);
  };
  visit(root);
  return out;
}

// One course's candidates, flat and in round order. `advanced` / `contested` are
// facts about a round's SET of candidates, so they're folded from the siblings —
// the node carries what only it knows, this carries what only the set knows.
export function courseCandidates(course: LineageNode): LineageCandidate[] {
  const cands = course.children.filter((c) => c.kind === "candidate");
  const labelById = new Map(cands.map((c) => [c.id, c.label]));
  const perRound = new Map<number, LineageNode[]>();
  for (const c of cands) {
    const arr = perRound.get(c.round ?? 0) ?? [];
    arr.push(c);
    perRound.set(c.round ?? 0, arr);
  }
  return cands.map((c) => {
    const siblings = perRound.get(c.round ?? 0) ?? [];
    // A fork carries the provenance of the course it is — that is what marks it apart
    // from an attempt L1 minted.
    const isFork = c.course_kind !== null;
    return {
      candidateId: c.id,
      label: c.label,
      round: c.round ?? 0,
      accuracy: c.accuracy,
      compositeFitness: c.composite_fitness,
      isWinner: c.is_winner,
      advanced: siblings.some((s) => s.is_winner),
      roundClosed: c.round_closed,
      contested: siblings.length > 1,
      isFork,
      cutFrom: isFork ? (labelById.get(c.parent_id ?? "") ?? null) : null,
    };
  });
}

// Every fork on the tree, keyed by its own cycle_id — what a surface that already has a
// fork (from the flat `/cycles` registry) asks for its place on the timeline.
export function forkAttempts(root: LineageNode): ReadonlyMap<string, LineageCandidate> {
  const m = new Map<string, LineageCandidate>();
  for (const course of walkCourses(root)) {
    for (const cand of courseCandidates(course)) {
      if (cand.isFork) m.set(cand.candidateId, cand);
    }
  }
  return m;
}

// `cycle_id` → its candidates, for every course in the tree.
export function candidatesByCourse(root: LineageNode): ReadonlyMap<string, LineageCandidate[]> {
  const m = new Map<string, LineageCandidate[]>();
  for (const course of walkCourses(root)) m.set(course.id, courseCandidates(course));
  return m;
}

