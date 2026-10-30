// Keyed on `reading.arm.label`: the joined rows come from the leaf's `dashboard.json`.

import type { SubtreeCourse } from "@/lib/api";
import type { CyclePath } from "@/lib/ids";
import { phaseWalks } from "./run-phase";
import { candidatesAtPath, type LineageIndex } from "./lineage-candidates";

export function innerPanelIndex(
  index: LineageIndex,
  path: CyclePath | null,
): ReadonlyMap<string, SubtreeCourse> {
  const m = new Map<string, SubtreeCourse>();
  for (const cand of candidatesAtPath(index, path)) {
    for (const run of cand.children.flatMap((c) => c.children)) {
      // No `task` means an interrupted mint; a wrong join is worse than an absent one.
      if (run.facts.task) m.set(panelCellKey(cand.facts.reading.arm.label, run.facts.task), run);
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

export function runningInnerRun(index: LineageIndex): SubtreeCourse | null {
  return (
    index.courses.find((c) => c.facts.mint === "inner" && phaseWalks(c.state.status.run_phase)) ?? null
  );
}
