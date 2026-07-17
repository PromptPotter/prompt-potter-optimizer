// An L4 course's PANEL: which inner campaign measured which cell.
//
// At L4 a "sample" is not a scored row — it is a whole inner campaign. The outer
// round file records one result per panel cell, carrying the cell's name in
// `query` (`justlogic-d23/seed-0`) and nothing else worth rendering: `is_hit` is
// null, `predicted`/`ground_truth` are empty. The measurement it stands for lives
// in the sandbox, as a campaign of its own.
//
// The join is the served tree's own edge — the course filed under the candidate —
// so BOTH halves come from one namespace. Reading the runs' `spawned_by` stamps
// instead got the fork case wrong: a fork's cells are stamped with the fork's
// PRIVATE counter (`C1.1`), while the timeline they render on renumbers them
// (`C1.4`). The tree does that renumbering; a raw stamp cannot.
//
// A cell whose run carries no `task` has no key and drops out of the lookup — an
// interrupted mint writes no provenance, and a wrong join is worse than an absent
// one. The row still renders, saying so.

import type { LineageNode } from "@/lib/api";
import { candidatesOf, childCourses } from "./lineage-candidates";

// `(candidate_label, cell)` → the inner run that measured it, over one course's timeline.
export function innerPanelIndex(course: LineageNode | null): ReadonlyMap<string, LineageNode> {
  const m = new Map<string, LineageNode>();
  for (const cand of candidatesOf(course ?? undefined)) {
    for (const run of childCourses(cand)) {
      if (run.task) m.set(panelCellKey(cand.label, run.task), run);
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
