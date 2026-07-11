// The registry itself is generated from application/scoring/evaluators.py
// (`EVALUATOR_META` in types.generated.ts) — this module owns only the panel's
// derivations over it. The hand-copy that used to live here listed 13 of the
// registry's 16 evaluators and described two of them wrongly.
import type { EvaluatorMeta } from "@/lib/api/types.generated";

export function whatifIdentifiersInFormula(formula: string | undefined | null): Set<string> {
  if (!formula) return new Set();
  const tokens = formula.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
  return new Set(tokens);
}

// Per-evaluator weight parsed from a scoring formula — the coefficient on each
// `<coef> * [(1 - ]name` term (the canonical weighted-sum shape every default /
// operator formula uses). Seeds the What-If weight sliders so they start at the
// evaluator's *actual* composite weight; the operator reweights from there. A
// formula that isn't a weighted sum simply yields no/partial entries → callers
// fall back to a default weight.
export function weightsFromFormula(formula: string | undefined | null): Record<string, number> {
  const out: Record<string, number> = {};
  if (!formula) return out;
  const re = /([0-9]*\.?[0-9]+)\s*\*\s*\(?\s*(?:1\s*-\s*)?([a-zA-Z_][a-zA-Z0-9_]*)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(formula)) !== null) {
    const [, coef, name] = m;
    if (coef == null || name == null) continue;
    if (!(name in out)) out[name] = parseFloat(coef);
  }
  return out;
}

export interface Row {
  displayName: string;
  registryName: string;
  applicable: boolean;
  description: string;
  direction: "high" | "low";
}

export function buildRows(meta: EvaluatorMeta[], applicable: Set<string>): Row[] {
  const out: Row[] = [];
  const used = new Set<string>();
  for (const m of meta) {
    const matches = [...applicable].filter((a) => a === m.name || a.endsWith("_" + m.name));
    if (matches.length === 0) {
      out.push({ displayName: m.name, registryName: m.name, applicable: false, description: m.description, direction: m.direction });
    } else {
      for (const an of matches) {
        out.push({ displayName: an, registryName: m.name, applicable: true, description: m.description, direction: m.direction });
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
