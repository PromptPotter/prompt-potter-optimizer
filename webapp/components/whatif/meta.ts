// Mirror of promptpotter/application/scoring/evaluators.py::evaluators_meta().
// Inline copy so the panel renders before — and even without — the API fetch.
// The dashboard.json snapshot still wins when present (it reflects the registry
// the *current cycle* was scored under).
// Lifted verbatim from webapp/index.html:1711.

export interface EvaluatorMeta {
  name: string;
  scope: "per_round" | "per_query";
  direction: "high" | "low";
  node_type: string | null;
  description: string;
}

export const WHATIF_INLINE_META: EvaluatorMeta[] = [
  { name: "accuracy",                 scope: "per_round", direction: "high", node_type: null,
    description: "Mean per-query score across the round's result set." },
  { name: "error_rate",               scope: "per_round", direction: "low",  node_type: null,
    description: "Fraction of queries that errored (ERROR predicted or exception)." },
  { name: "degraded_rate",            scope: "per_round", direction: "low",  node_type: null,
    description: "Fraction of queries that completed with pipeline degradation warnings." },
  { name: "runtime_failure_rate",     scope: "per_round", direction: "low",  node_type: null,
    description: "Runtime failure count on OptSP memory, normalized by total queries." },
  { name: "latency_norm",             scope: "per_round", direction: "high", node_type: null,
    description: "Mean latency normalized against LATENCY_BUDGET_MS (1.0 = instant, 0.0 = ≥ budget)." },
  { name: "source_recall",            scope: "per_round", direction: "high", node_type: "candidate_source",
    description: "Fraction of queries where GT appears in a candidate_source node's output." },
  { name: "candidate_recall",         scope: "per_round", direction: "high", node_type: "ranker",
    description: "Fraction of queries where GT appears in a ranker node's final_ranking." },
  { name: "cache_hit_rate",           scope: "per_round", direction: "high", node_type: "cache",
    description: "Fraction of queries resolved by a cache node (non-null timing)." },
  { name: "retrieval_shortfall",      scope: "per_query", direction: "high", node_type: null,
    description: "Per-query min(observed/target, 1.0) across nodes with max_*/num_* limits." },
  { name: "mean_retrieval_shortfall", scope: "per_round", direction: "high", node_type: null,
    description: "Mean of retrieval_shortfall across the round's results." },
  { name: "pipeline_compactness",     scope: "per_round", direction: "low",  node_type: null,
    description: "1 − (active_steps − 1)/11 — shorter pipelines score higher." },
  { name: "prompt_compactness",       scope: "per_round", direction: "high", node_type: null,
    description: "1 − len(rendered_prompt)/PROMPT_BUDGET_CHARS — shorter prompts score higher." },
];

export function whatifIdentifiersInFormula(formula: string | undefined | null): Set<string> {
  if (!formula) return new Set();
  const tokens = formula.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
  return new Set(tokens);
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
