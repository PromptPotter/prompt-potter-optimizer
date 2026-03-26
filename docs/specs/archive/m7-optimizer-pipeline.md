# Milestone 7: Optimizer-as-Pipeline — Complete

**Version:** 2.0.0 | **Completed:** 2026-03-26 | **Depends on:** M6 PipelineSchema

---

## Context

The optimizer itself is a pipeline with 5 nodes organized into 4 named sequences, modeled using the same PipelineSchema architecture as the target backend. This solved three problems: **tracing** (optimizer nodes get Langfuse observations), **reproducibility** (every meta-optimizer decision traced with full I/O), **self-optimization** (L4 meta-PromptPotter path).

Pre-M7, artifacts like critique_text, thinking_styles, plan, L2/L3 transition rationale, and candidate generation prompts were lost after each cycle. M7 solved all of these via `OptSearchPoint` checkpointing in trial JSON + node-level tracing via `observed_step()`.

---

## Architectural Decisions

### ADR-1: Critique inside l1_evaluate, not orchestrator
Critique output feeds the next round's generation — it's part of l1_evaluate's output contract. Keeps Langfuse traces clean.

### ADR-2: `baseline_rendered` in `cycle_config_identity()`
Removing it orphans existing campaign data (cycle_id changes). Non-determinism from restructure_context() is acceptable.

### ADR-3: `scan_context` as runtime input, not node config
Scan context can change between rounds (e.g., after L2 transition). Node config is declaration-time; input data varies per invocation.

### ADR-4: Suggestion generation stays in orchestrator
Suggestions require accumulated round history + campaign config — orchestrator-level state that shouldn't be threaded through nodes.

### ADR-5: Observation type per node
Most nodes = `"generation"` (single LLM call). l1_evaluate = `"span"` (composite: N evals + critique).

### ADR-6: OptSearchPoint as cross-reference, not container
OptSearchPoint holds optimizer config + `content_hashes` linking to target-layer dataset_runs. No data duplication between layers.

### ADR-7: Double-brace template syntax
`{{variable}}` avoids conflicts with JSON examples in prompt text. `compile(**kwargs)` does substitution.

### ADR-8: OptSearchPoint is mutable, not frozen
Enables in-place updates during feedback cycle, single `model_dump()` at checkpoint time. All optimizer-state fields consolidated from scattered _LoopState onto OptSearchPoint.

---

## Key Design Artifacts

**Pipeline declaration:** `api/config/optimizer_pipeline.json` — 5 nodes (l1_generate, l1_evaluate, critique, l2_refine_context, l3_modify_plan), 4 sequences (l1_round, l1_round_with_critique, l2_escalation, l3_escalation).

**Node type hierarchy:** `llm` → `llm/structured` → `llm/meta`, plus `agent`, `evaluation`, `deterministic`, `web_search`.

**Shared primitives:** `llm_call()` in `api/config/optimizer_pipeline.py` (config-driven LLM wrapper), `observed_step()` in `api/services/obs/node_tracer.py` (async tracing context manager).

**Responsibility matrix:** l1_generate decides pipeline_params; critique decides focus areas; l2_refine_context decides context + meta-settings; l3_modify_plan decides strategic plan.

**Warning inventory:** Per-query cross-round tracking on `OptSearchPoint.warning_inventory`. L2 probe rounds test warned queries with new settings. `l2_directive` bridges L2 diagnostic reasoning to L1 generation (sliding window of 1).

**Kernel hang root cause (Windows):** Copying live httpx `AsyncClient` objects in `finally` blocks on a corrupted event loop hangs indefinitely. Design rules: no live objects in metrics metadata, daemon threads for Langfuse, no `asyncio.shield` in eval path.

---

## Exit Gate — Passed

Optimizer pipeline traced end-to-end with full reproducibility. Given a trial JSON, every LLM call in the optimization cycle can be reconstructed.
