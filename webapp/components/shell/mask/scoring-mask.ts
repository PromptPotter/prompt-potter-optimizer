"use client";
// The SCORING MASK — the browser's half of `docs/operations/mask-projection.md`, bound by
// `webapp/CLAUDE.md` § Scoring authority.

import { useSyncExternalStore } from "react";
import { CELL_TERM_META, type CellTermMeta } from "@/lib/api/types.generated";

// Weight for a selected term the served decomposition carries no coefficient for.
export const DEFAULT_MASK_WEIGHT = 0.1;

export type ScoringMask =
  | { kind: "weights"; selected: ReadonlySet<string>; weights: Readonly<Record<string, number>> }
  | { kind: "expression"; lens: string };

// A function, not a shared constant: the arm carries a mutable Set.
export function emptyMask(): ScoringMask {
  return { kind: "weights", selected: new Set(), weights: {} };
}

// A menu highlight only, never a scoring read: slider coefficients are served
// (`composite_fitness_weights`).
export function identifiersInFormula(formula: string | undefined | null): Set<string> {
  if (!formula) return new Set();
  return new Set(formula.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || []);
}

// One tile per term a `per_cell` formula can name, plus any the realized formula names beyond
// the served vocabulary (a judge's term) — so a seeded weight always has a tile to sit on.
export function termRows(extra: Iterable<string> = []): CellTermMeta[] {
  const known = new Set(CELL_TERM_META.map((m) => m.name));
  const unknown = [...extra].filter((name) => !known.has(name));
  return [
    ...CELL_TERM_META,
    ...unknown.map((name) => ({ name, direction: "high" as const, description: "" })),
  ];
}

// A "low" term flips to `(1 - name)`, matching the server composite's shape so seeded weights
// reproduce the realized criterion.
function formulaFromWeights(mask: Extract<ScoringMask, { kind: "weights" }>): string | null {
  const terms: string[] = [];
  for (const sel of mask.selected) {
    const w = mask.weights[sel] ?? DEFAULT_MASK_WEIGHT;
    if (w === 0) continue;
    const low = CELL_TERM_META.find((m) => m.name === sel)?.direction === "low";
    terms.push(`${w} * ${low ? `(1 - ${sel})` : sel}`);
  }
  return terms.length > 0 ? terms.join(" + ") : null;
}

const SCORE = "score:";

export function lensOf(mask: ScoringMask | null): string | null {
  if (mask == null) return null;
  if (mask.kind === "expression") return mask.lens.trim() || null;
  const formula = formulaFromWeights(mask);
  return formula ? SCORE + formula : null;
}

// The bare `per_cell` formula a `score:` lens names — what a fork applying it carries as
// `scoring.per_cell`; `null` for an `abort:` lens.
export function criterionOf(mask: ScoringMask | null): string | null {
  const lens = lensOf(mask);
  return lens?.startsWith(SCORE) ? lens.slice(SCORE.length) : null;
}

// Module state, not a context: the candidates card and `lib/lineage.tsx` share no ancestor.
// Compare never reads it — each channel's mask rides its own address.

interface MaskState {
  open: boolean;
  mask: ScoringMask;
  // A fresh cycle re-seeds against its own formula rather than inheriting these picks.
  seededForCycle: string | null;
}

let state: MaskState = { open: false, mask: emptyMask(), seededForCycle: null };
const listeners = new Set<() => void>();

export function setScoringMask(patch: Partial<MaskState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function useScoringMask(): MaskState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
    () => state,
    () => state,
  );
}
