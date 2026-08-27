"use client";
// The SCORING MASK — an alternative criterion projected over the record, and the browser's half of
// `docs/operations/mask-projection.md`.
//
// The repo already names this: a *mask* is the alternative criterion, a *lens* is the query
// parameter that picks one (`?lens=`, `;lens=`). "What-if" was a third word for the same thing,
// coined here before the vocabulary existed — and there are two mask kinds, so this one is the
// SCORING mask, as against the `abort:` mask the lens menu offers.
//
// It is a DISCRIMINATED UNION rather than the wire's flat `{lens, samples}` because the editor
// needs the structured form the wire has already collapsed: the evaluator SET is what
// `subsetExactFor` and the rank summary read, and flattening to a formula string would put formula
// parsing back in the browser — the scoring read `webapp/CLAUDE.md` § Scoring authority forbids.
// The address is derived when the fetch key is built (`lensOf`), never held beside the value.
//
// The `expression` arm is what the grid cannot represent: a hand-typed lens in whatever namespace
// the server accepts, carried verbatim. Nothing here splits or rewrites one.

import { useSyncExternalStore } from "react";
import { EVALUATOR_META, type EvaluatorMeta } from "@/lib/api/types.generated";

// Default weight for a selected evaluator the served decomposition carries no coefficient for (the
// operator added one the realized formula does not name). Modest, like a secondary composite term
// — visible, not dominant; crank the slider to amplify.
export const DEFAULT_MASK_WEIGHT = 0.1;

export type ScoringMask =
  | { kind: "weights"; selected: ReadonlySet<string>; weights: Readonly<Record<string, number>> }
  | { kind: "expression"; lens: string };

// A FUNCTION, not a shared constant: the arm carries a Set, and one module-level instance handed
// to every caller is a mutable default waiting for the first `.add` that forgets to copy.
export function emptyMask(): ScoringMask {
  return { kind: "weights", selected: new Set(), weights: {} };
}

// The registry entry a display name resolves to — a node-bound evaluator arrives prefixed
// (`ranker_source_recall`), so the suffix match is the rule, and it is ONE rule because a second
// spelling of it is one that can disagree about the same tile.
function metaFor(name: string): EvaluatorMeta | undefined {
  return EVALUATOR_META.find((m) => name === m.name || name.endsWith("_" + m.name));
}

// Which evaluators a formula NAMES — a menu highlight, filtered by the caller against the
// applicable set so a function name cannot light a tile. Not a scoring read: the coefficients the
// sliders seed from are served (`composite_fitness_weights`), because parsing those here meant
// substituting a default for every term the pattern missed, with nothing on screen saying so.
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

// The selection + per-evaluator weights as a backend round-scorer formula — a weighted sum
// `w1*t1 + …` with each "low" evaluator flipped to `(1 - name)`, matching the composite's own
// shape so seeded weights ≈ the realized criterion and reweighting one term actually moves the
// ranking. The value itself is computed server-side and served back; nothing here scores.
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

// The mask as the ONE lens string both addresses carry — the tree's `?lens=` and the subject
// grammar's `;lens=`. A typed expression rides verbatim, so a namespace the server grows needs no
// edit on this side.
export function lensOf(mask: ScoringMask | null): string | null {
  if (mask == null) return null;
  if (mask.kind === "expression") return mask.lens.trim() || null;
  const formula = formulaFromWeights(mask);
  return formula ? SCORE + formula : null;
}

// The bare FORMULA — what `CampaignConfig.scoring` takes — as against the namespaced lens the read
// grammar wants. `null` where the mask carries no criterion, or carries one in another namespace
// (`abort:`), which is a projection with no config to apply it as. Both spellings are minted here,
// so no consumer strips a prefix this module added.
export function criterionOf(mask: ScoringMask | null): string | null {
  const lens = lensOf(mask);
  return lens?.startsWith(SCORE) ? lens.slice(SCORE.length) : null;
}

// Whether this criterion re-scores EXACTLY under a SAMPLE-SET mask. Only the row-derivable
// evaluators recompute from the filtered rows (`mask/load.py::materialize_row_derivable`); the rest
// are read off the full-set snapshot, so a criterion mixing the two adds a subset number to a
// whole-set one and reports the sum as though the whole of it were the subset's.
//
// Reading the served registry's `from_rows` is a READABILITY question, not a recompute. A typed
// expression is judged on its identifiers: a token naming no evaluator (a function, a literal
// term) answers `undefined` and the whole criterion is suppressed — conservative in the one
// direction that cannot mislead.
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

// ── The dashboard's mask, cross-mount ───────────────────────────────────────
// Module-scoped rather than a context because TWO SUBTREES read it and they are not in an
// ancestor/descendant relation: the candidates card, and `lib/lineage.tsx`, which turns the mask
// into the served tree's `?lens=`. There is no component containing both.
//
// Compare does NOT read this. Each of its channels holds its own mask ON ITS ADDRESS — that is
// what a channel IS — so one board can carry the record and two counterfactuals of it at once. The
// EDITOR is shared; the ownership is not, and collapsing them would give every channel the same
// mask.

interface MaskState {
  open: boolean;
  mask: ScoringMask;
  // Which cycle the mask was seeded for. Binding a fresh cycle re-seeds against THAT cycle's
  // formula rather than inheriting the last one's picks.
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
