// An L4 course's PANEL: which inner campaign measured which cell.
//
// At L4 a "sample" is not a scored row — it is a whole inner campaign. The outer
// round file records one result per panel cell, carrying the cell's name in
// `query` (`justlogic-d23/seed-0`) and nothing else worth rendering: `is_hit` is
// null, `predicted`/`ground_truth` are empty. The measurement it stands for lives
// in the sandbox, as a campaign of its own.
//
// `spawned_by` is what joins them: the engine stamps the SAME two strings it
// scored under — `task` (the cell) and `candidate_label` (who asked) — so a cell
// resolves to its run by lookup, never by order. Both halves are needed: seed-0
// measured for C0 and seed-0 measured for C1.1 are different runs of the same
// declaration.
//
// TWO predicates, decided here once: IS IT A CELL? (its own root cycle — a fork
// continues a cell's run, so it belongs to that cell's story, not beside it) and
// IS IT FILED? (it carries `spawned_by`; an interrupted mint writes none, and a
// wrong join is worse than an absent one). An unfiled cell has no panel key and
// drops out of the lookup; the sidebar lists it loose under its course instead.
//
// This resolves a CELL of the outer round file, which is why it still joins on the
// stamp. It is not the genealogy: "which runs hang off this candidate" is answered
// by the served tree (`/tree`), and asking it here as well is how the same edge came
// to have two answers.

import type { CycleListEntry } from "@/lib/api";
import { rootCycleId } from "@/lib/ids";

// Every cell of a course's sandbox — the runs it fanned out, forks excluded.
function innerCells(cycles: readonly CycleListEntry[]): CycleListEntry[] {
  return cycles.filter((c) => rootCycleId(c.cycle_id) === c.cycle_id);
}

// `(candidate_label, cell)` → the inner cycle that measured it. Both halves of the
// stamp are needed, so an unfiled cell has no key and drops out.
export function innerPanelIndex(
  cycles: readonly CycleListEntry[],
): ReadonlyMap<string, CycleListEntry> {
  const m = new Map<string, CycleListEntry>();
  for (const c of innerCells(cycles)) {
    const sb = c.spawned_by;
    if (!sb?.candidate_label || !sb.task) continue;
    m.set(panelCellKey(sb.candidate_label, sb.task), c);
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
