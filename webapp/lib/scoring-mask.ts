"use client";

import { CELL_TERM_META, type CellTermMeta } from "@/lib/api/types.generated";
import { createModuleStore } from "@/lib/module-store";

// A dial at weight 0 is off, so the weights ARE the selection.
export type ScoringMask =
  | { kind: "dials"; weights: Readonly<Record<string, number>> }
  | { kind: "expression"; lens: string };

export const NO_DIALS: ScoringMask = { kind: "dials", weights: {} };

export const DIAL_TERMS: readonly CellTermMeta[] = CELL_TERM_META.filter((m) => m.dial !== null);

// The spelling `compiler.py::parse_dials` reads; served vocabulary order, so one set is one string.
export function dialsText(weights: Readonly<Record<string, number>>): string {
  return DIAL_TERMS.flatMap((m) => {
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

export function lensOf(mask: ScoringMask | null): string | null {
  if (mask == null) return null;
  if (mask.kind === "expression") return mask.lens.trim() || null;
  const text = dialsText(mask.weights);
  return text ? DIALS + text : null;
}

// Module state, not a context: the candidates card and `lib/lineage.tsx` share no ancestor.
interface MaskState {
  open: boolean;
  mask: ScoringMask;
  seededForCycle: string | null;
}

const store = createModuleStore<MaskState>({ open: false, mask: NO_DIALS, seededForCycle: null });

export const setScoringMask = store.set;

export function useScoringMask(): MaskState {
  return store.useStore();
}
