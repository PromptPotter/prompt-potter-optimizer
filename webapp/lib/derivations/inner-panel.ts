// An L4 course's PANEL: which inner campaign measured which cell.
//
// At L4 a "sample" is not a scored row — it is a whole inner campaign. The outer
// round file records one result per panel cell, carrying the cell's name in
// `query` (`justlogic-d23/seed-0`) and nothing else worth rendering; the
// measurement it stands for lives in the sandbox, as a campaign of its own.
//
// The join is the served tree's own edge — the course filed under the candidate —
// so both halves come from one namespace. KEYED ON `course_label`, NOT `label`:
// the rows joined against come from the LEAF's per-cycle `dashboard.json`, which
// speaks the minting course's private counter — see `course_label` on the
// generated `LineageNode` type for the full argument.
//
// A cell whose run carries no `task` has no key and drops out of the lookup — an
// interrupted mint writes no provenance, and a wrong join is worse than an absent
// one. The row still renders, saying so.

import type { LineageNode } from "@/lib/api";
import type { CyclePath } from "@/lib/ids";
import { candidatesAtPath, childCourses } from "./lineage-candidates";

// `(course_label, cell)` → the inner run that measured it, over the timeline of the course
// at `path` inside the one served `tree`.
export function innerPanelIndex(
  tree: LineageNode | null,
  path: CyclePath | null,
): ReadonlyMap<string, LineageNode> {
  const m = new Map<string, LineageNode>();
  if (!tree || !path) return m;
  for (const cand of candidatesAtPath(tree, path)) {
    for (const run of childCourses(cand)) {
      if (run.task) m.set(panelCellKey(cand.course_label, run.task), run);
    }
  }
  return m;
}

// The panel's composite key. `CELL_SEP` can occur in neither half — a label is
// `C{round}.{idx}`, and a task is `{dataset}/seed-{n}` over the id charset
// (`^[a-zA-Z0-9_.-]+$` on the Python side) — so the pair round-trips unambiguously,
// the same argument `ids.ts::unitKey` makes for `::`.
const CELL_SEP = "::";

export function panelCellKey(candidateLabel: string, cell: string): string {
  return `${candidateLabel}${CELL_SEP}${cell}`;
}

// The cell's short name. Every cell of one panel runs the same benchmark, so the
// leading `{dataset}/` is the same string on every row and only the seed tells
// them apart — the same reason an inner run's sidebar row wears its task tail.
export function panelCellLabel(cell: string): string {
  const slash = cell.lastIndexOf("/");
  return slash >= 0 ? cell.slice(slash + 1) : cell;
}

// The inner cycle running RIGHT NOW, if any — the remote strip's drill target while
// the outer is viewed. Whole-tree walk so a nested (L5+) run still resolves;
// `run_phase` is course-only by construction, so candidates never match.
export function runningInnerRun(root: LineageNode | null): LineageNode | null {
  if (!root) return null;
  const stack: LineageNode[] = [...(root.children ?? [])];
  while (stack.length) {
    const n = stack.pop()!;
    if (n.kind === "course" && n.course_kind === "inner" && n.run_phase === "running") return n;
    if (n.children) stack.push(...n.children);
  }
  return null;
}
