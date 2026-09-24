"use client";
// The SCORING MASK — the browser's half of `docs/operations/mask-projection.md`, bound by
// `webapp/CLAUDE.md` § Scoring authority.

import { useSyncExternalStore } from "react";
import { EVALUATOR_META, type EvaluatorMeta } from "@/lib/api/types.generated";

// Weight for a selected evaluator the served decomposition carries no coefficient for.
export const DEFAULT_MASK_WEIGHT = 0.1;

export type ScoringMask =
  | { kind: "weights"; selected: ReadonlySet<string>; weights: Readonly<Record<string, number>> }
  | { kind: "expression"; lens: string };

// A function, not a shared constant: the arm carries a mutable Set.
export function emptyMask(): ScoringMask {
  return { kind: "weights", selected: new Set(), weights: {} };
}

// A node-bound evaluator arrives prefixed (`ranker_source_recall`), so the suffix match is the rule.
function metaFor(name: string): EvaluatorMeta | undefined {
  return EVALUATOR_META.find((m) => name === m.name || name.endsWith("_" + m.name));
}

// A menu highlight only, never a scoring read: slider coefficients are served
// (`composite_fitness_weights`).
export function identifiersInFormula(formula: string | undefined | null): Set<string> {
  if (!formula) return new Set();
  return new Set(formula.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || []);
}

export interface Row {
  displayName: string;
  registryName: string;
  applicable: boolean;
  description: string;
  direction: "high" | "low";
}

export function buildRows(meta: readonly EvaluatorMeta[], applicable: Set<string>): Row[] {
  const out: Row[] = [];
  const used = new Set<string>();
  for (const m of meta) {
    const matches = [...applicable].filter((a) => a === m.name || a.endsWith("_" + m.name));
    if (matches.length === 0) {
      out.push({
        displayName: m.name,
        registryName: m.name,
        applicable: false,
        description: m.description,
        direction: m.direction,
      });
    } else {
      for (const an of matches) {
        out.push({
          displayName: an,
          registryName: m.name,
          applicable: true,
          description: m.description,
          direction: m.direction,
        });
        used.add(an);
      }
    }
  }
  for (const an of applicable) {
    if (used.has(an)) continue;
    out.push({ displayName: an, registryName: an, applicable: true, description: "", direction: "high" });
  }
  return out;
}

// A "low" evaluator flips to `(1 - name)`, matching the server composite's shape so seeded
// weights reproduce the realized criterion.
function formulaFromWeights(mask: Extract<ScoringMask, { kind: "weights" }>): string | null {
  const terms: string[] = [];
  for (const sel of mask.selected) {
    const w = mask.weights[sel] ?? DEFAULT_MASK_WEIGHT;
    if (w === 0) continue;
    terms.push(`${w} * ${metaFor(sel)?.direction === "low" ? `(1 - ${sel})` : sel}`);
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

// The bare formula `CampaignConfig.scoring` takes; `null` for an `abort:` lens.
export function criterionOf(mask: ScoringMask | null): string | null {
  const lens = lensOf(mask);
  return lens?.startsWith(SCORE) ? lens.slice(SCORE.length) : null;
}

// Only row-derivable evaluators recompute under a sample-set mask
// (`mask/load.py::materialize_row_derivable`); the rest are whole-set, so mixing them misleads.
export function subsetExactFor(mask: ScoringMask | null): boolean {
  if (mask == null) return false;
  const names = mask.kind === "weights" ? mask.selected : identifiersInFormula(mask.lens);
  let any = false;
  for (const name of names) {
    if (!metaFor(name)?.from_rows) return false;
    any = true;
  }
  return any;
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
