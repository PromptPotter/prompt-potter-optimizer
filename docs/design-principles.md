# Design Principles

What's genuinely distinctive about how PromptPotter works.

---

## Prompt decomposition & variant library

Backends have monolithic prompts — one big string. PromptPotter decomposes that into independent fields (`persona`, `task_intent`, `thinking_style`, `answer_format`, `problem_description`) via LLM restructure, then perturbs each field independently using a default library of text-string variants (`api/config/prompt_variants.json`).

This is the core architectural move. It turns one opaque prompt into a combinatorial search space where sensitivity scan can measure each axis independently, grid search can explore the cross-product, and the feedback cycle can mutate specific fields. Without decomposition, optimization is blind rewriting of a monolith.

## SearchPoint as atomic unit

`SearchPoint` bundles `prompt_state` + `model` + `temperature` + `pipeline_params` into one frozen object. All mutations via `.derive(**changes)`. Content-hashable, prevents accidental mutation of shared state.

## Prompt alias groups

Alias groups (`register_alias` / `resolve_aliases`) link equivalent prompt hashes so historical data is discoverable across forms. Resolution is transitive — if A=B and B=C, querying any returns {A, B, C}.

The main use case: linking the backend's original monolithic prompt to its LLM-restructured decomposed form, so evaluations against either count as the same prompt.

## No backward compatibility

No shims, no dual-format readers, no fallback paths. Old data is regenerated, not supported. Every code path stays exercised.

## Direct field access for guaranteed fields

`dict[key]` instead of `.get(key, fallback)` when a field is structurally guaranteed. Surfaces schema violations immediately rather than hiding them behind silent defaults.

## Graceful interrupt & partial persistence

Backend evaluation batches use a **signal-flag pattern**: the first Ctrl+C lets the in-flight backend call finish (its result is printed and saved), then stops the loop. A second Ctrl+C force-quits immediately. **No completed work is ever discarded.**

Implementation: `evaluate_prompt_batch()` installs a temporary SIGINT handler that sets a `_stop_requested` flag on first press and raises `KeyboardInterrupt` on second press. The in-flight `await backend_reranker_eval()` runs to completion uninterrupted because the signal handler no longer raises. After the current query finishes and its result is appended + displayed, the loop checks the flag and exits cleanly.

Partial runs are persisted to `dataset_runs/` with a `"partial": True` flag. On re-run, `find_cached_queries()` discovers individual query results by `sp_hash` and skips re-evaluation — only uncompleted queries hit the backend. When the full batch eventually completes, the partial entry is replaced automatically (same `content_hash`).

All interrupt handlers must catch both `KeyboardInterrupt` and `asyncio.CancelledError`. Notebook cells are interruptible units of work. Disk writes happen incrementally. When interrupted, the cell skips non-essential work (critique, suggestions, obs logging, cloud sync) and returns immediately.

## Display parity

Cached and fresh results use the same output format (fields, layout, ordering). A provenance indicator (📖 for cached, no marker for live) distinguishes data source for transparency — the user should know whether results are replayed or freshly computed. All other formatting (accuracy, CI, delta, hit/miss) is identical regardless of source.
