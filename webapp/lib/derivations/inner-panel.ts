// An L4 course's panel: which inner campaign measured which cell. Keyed on `course_label`, NOT
// `label` — the joined rows come from the leaf's `dashboard.json`, the minting course's counter.

import type { LineageNode } from "@/lib/api";
import type { CyclePath } from "@/lib/ids";
import { candidatesAtPath, childCourses } from "./lineage-candidates";

export function innerPanelIndex(
  tree: LineageNode | null,
  path: CyclePath | null,
): ReadonlyMap<string, LineageNode> {
  const m = new Map<string, LineageNode>();
  if (!tree || !path) return m;
  for (const cand of candidatesAtPath(tree, path)) {
    for (const run of childCourses(cand)) {
      // No `task` means an interrupted mint; a wrong join is worse than an absent one.
      if (run.task) m.set(panelCellKey(cand.course_label, run.task), run);
    }
  }
  return m;
}

// In neither half: a label is `C{round}.{idx}`, a task `{dataset}/seed-{n}` over the Python id
// charset `^[a-zA-Z0-9_.-]+$`.
const CELL_SEP = "::";

export function panelCellKey(candidateLabel: string, cell: string): string {
  return `${candidateLabel}${CELL_SEP}${cell}`;
}

// Every cell of one panel runs the same benchmark, so only the seed tells rows apart.
export function panelCellLabel(cell: string): string {
  const slash = cell.lastIndexOf("/");
  return slash >= 0 ? cell.slice(slash + 1) : cell;
}

// Whole-tree walk so a nested (L5+) run still resolves; `run_phase` is course-only.
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
