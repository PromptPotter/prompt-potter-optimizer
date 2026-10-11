// Keyed on `reading.arm.label`, NOT the timeline `label`: the joined rows come from the leaf's `dashboard.json`.

import type { CourseNode } from "@/lib/api";
import type { CyclePath } from "@/lib/ids";
import { phaseWalks } from "@/lib/run-phase";
import { candidatesAtPath, walkCourses, type LineageIndex } from "./lineage-candidates";

export function innerPanelIndex(
  index: LineageIndex,
  path: CyclePath | null,
): ReadonlyMap<string, CourseNode> {
  const m = new Map<string, CourseNode>();
  for (const cand of candidatesAtPath(index, path)) {
    for (const run of cand.children) {
      // No `task` means an interrupted mint; a wrong join is worse than an absent one.
      if (run.task) m.set(panelCellKey(cand.reading.arm.label, run.task), run);
    }
  }
  return m;
}

// In neither half: a label is `C{round}.{idx}`, a task `{dataset}/seed-{n}` over `^[a-zA-Z0-9_.-]+$`.
const CELL_SEP = "::";

export function panelCellKey(candidateLabel: string, cell: string): string {
  return `${candidateLabel}${CELL_SEP}${cell}`;
}

export function panelCellLabel(cell: string): string {
  const slash = cell.lastIndexOf("/");
  return slash >= 0 ? cell.slice(slash + 1) : cell;
}

export function runningInnerRun(root: CourseNode | null): CourseNode | null {
  if (!root) return null;
  return (
    walkCourses(root).find((c) => c.course_kind === "inner" && phaseWalks(c.run_phase)) ?? null
  );
}
