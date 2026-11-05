import { CELL_TERM_META, type CellTerm, type ServedDashboard } from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";
import { createModuleStore } from "@/lib/state/module-store";

// A dial at weight 0 is off, so the weights ARE the selection.
export type ScoringMask =
  | { kind: "dials"; weights: Readonly<Record<string, number>> }
  | { kind: "expression"; lens: string };

export const NO_DIALS: ScoringMask = { kind: "dials", weights: {} };

const CORE_TERMS: readonly CellTerm[] = wordRows(CELL_TERM_META).map(([, m]) => m);

// The core's dials, then the campaign connector's own (`useConnector().cellTerms`).
export function dialTerms(own: readonly CellTerm[]): readonly CellTerm[] {
  return [...CORE_TERMS, ...own].filter((m) => m.dial !== null);
}

// The spelling `compiler.py::parse_dials` reads; served vocabulary order, so one set is one string.
export function dialsText(
  weights: Readonly<Record<string, number>>,
  own: readonly CellTerm[],
): string {
  return dialTerms(own).flatMap((m) => {
    const w = weights[m.name];
    return w !== undefined && w > 0 ? [`${m.name}=${w}`] : [];
  }).join(",");
}

export function dialsOf(text: string): Record<string, number> {
  const weights: Record<string, number> = {};
  for (const pair of text.split(",")) {
    const [name, raw] = pair.split("=").map((part) => part.trim());
    const weight = Number(raw);
    if (name && raw && Number.isFinite(weight)) weights[name] = weight;
  }
  return weights;
}

export function servedMask(
  formula: string | null | undefined,
  weights: Readonly<Record<string, number>> | null | undefined,
): ScoringMask {
  if (weights) return { kind: "dials", weights };
  return formula ? { kind: "expression", lens: SCORE + formula } : NO_DIALS;
}

const SCORE = "score:";
const DIALS = "dials:";

export function lensOf(mask: ScoringMask | null, own: readonly CellTerm[]): string | null {
  if (mask == null) return null;
  if (mask.kind === "expression") return mask.lens.trim() || null;
  const text = dialsText(mask.weights, own);
  return text ? DIALS + text : null;
}

// Module state, not a context: the candidates card and `lib/lineage.tsx` share no ancestor.
export interface MaskState {
  open: boolean;
  mask: ScoringMask;
  seededForCycle: string | null;
}

const store = createModuleStore<MaskState>({ open: false, mask: NO_DIALS, seededForCycle: null });

export const setScoringMask = store.set;

// Every mask reader reads the store alone, never the dashboard: a reader keyed on it (the time ray,
// a sidebar row's tree) re-renders only when the mask moves, never on a dashboard landing. Until
// `seededForCycle` names the viewed cycle, its mask is not known and a tree under it is held.
export const useMaskState = store.useStore;

// The viewed cycle's mask is its served criterion until the operator edits it: seeded once per
// viewed cycle, where its dashboard lands (`poll.tsx::useViewedDashboard`). A dashboard serving no
// formula (a run that died in INIT) seeds no dials.
export function seededMask(
  state: MaskState,
  cycleId: string,
  dash: Pick<ServedDashboard, "composite_fitness_formula" | "composite_fitness_weights">,
): MaskState {
  if (state.seededForCycle === cycleId) return state;
  return {
    open: state.open,
    mask: servedMask(dash.composite_fitness_formula, dash.composite_fitness_weights),
    seededForCycle: cycleId,
  };
}

export function seedScoringMask(cycleId: string, dash: ServedDashboard): void {
  const state = store.get();
  const next = seededMask(state, cycleId, dash);
  if (next !== state) store.set(next);
}
