# AIME 2025 — Dataset Context

30 competition math problems (AIME I + II 2025) from HuggingFace `MathArena/aime_2025`, integer
answer in [0, 999]. Loader `load_aime_2025`; scorer `aime_match` extracts `\boxed{N}` (the standard
math-benchmark convention) or falls back to the last number, then compares as `int` — matching
MathArena's own methodology.

**In band, not saturated** — measured origin and the five-route model A/B behind the pin are owned by
[`../../docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md)
§ AIME 2025 model A/B/C tests. Its limit is **size**: 30 problems cannot carry a config/test split,
so it serves as a head-to-head citation point, never as a population-grade instance.

## Why `max_tokens` is overridden here

`campaign.yaml::pipeline_overrides.llm_only` raises the cap — the one dataset that does. AIME-depth
reasoning chains hit the provider's default output ceiling and return `finish_reason=length`, and
the failure is not spread evenly: the *same* handful of problems truncate across every candidate
(sample #8 parabola, #10 piecewise linear, #6 twelve-letter combinatorics). Left capped, those rows
score zero for every arm and the deeper-reasoning candidate is punished for reasoning. Re-check the
cap against whatever model is pinned — the ceiling is per-model, so the override is a floor for the
hardest rows, not a constant.
