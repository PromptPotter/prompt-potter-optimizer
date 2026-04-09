# Security Foundations

**Version:** 1.0.0
**Date:** 2026-04-09
**Status:** Implemented
**Scope:** PromptPotter (eval security gate) + TermNorm (`llm_only` pipeline step)

---

## Context

PromptPotter's optimizer holds LLM API keys for its own meta-reasoning (L1/L2/L3/critique). These keys must never be used for evaluation inference by default. Evaluation queries go through a backend server (TermNorm today, others via ConnectorProtocol in M11) that holds its own inference keys.

For benchmark datasets (GSM8K, HotPotQA), two evaluation paths exist:

1. **Backend-routed** (default) — `BackendClient` sends queries to TermNorm's `/matches` endpoint with `steps=["llm_only"]`. The backend holds inference keys. PromptPotter never sees them.

2. **Local LLM-only** (opt-in, gated) — `LLMOnlyAdapter` calls LLMs directly using the server's optimizer API keys. No backend needed. Gated behind admin-set secret because it costs money and must not be accidentally enabled, especially in multi-tenant self-hosted deployments.

This is the first security layer toward a secure webapp architecture. The patterns here (secret-gated access, single validation function, forward-compatible auth) apply to future features as PromptPotter moves toward whitelabel distribution.

---

## Part 1: PromptPotter Evaluation Security Gate

### Architecture

```
campaign.json                       .env (server admin)
  "dataset_type": "llm-only"         LOCAL_EVAL_SECRET=<secret>
  "local_eval_token": "<token>"
         |                                  |
         v                                  v
    init_services()  --->  _validate_local_eval_access()
         |                         |
         |                hmac.compare_digest(token, secret)
         |                         |
         v                         v
    LLMOnlyAdapter            ValueError (clear message)
    (authorized)              on any mismatch
```

### Settings

**File:** `promptpotter/config/settings.py`

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LOCAL_EVAL_SECRET` | `str` | `""` | Admin-set secret. Empty = local eval disabled entirely. |

Empty by default. The admin of a self-hosted instance sets this in `.env` to allow local eval. Without it, all eval goes through the backend — no exceptions.

### Campaign Config Fields

**File:** `promptpotter/services/campaign/config.py` — `CampaignConfig`

| Field | Type | Description |
|-------|------|-------------|
| `dataset_type` | `str` | `"llm-only"` triggers `LLMOnlyAdapter` instead of `BackendClient` |
| `local_eval_token` | `str` | Must match `LOCAL_EVAL_SECRET` when `dataset_type == "llm-only"` |

`local_eval_token` is never committed to the repo. Users add it to their local `campaign.json` or pass via CLI. This prevents accidental cost from repo clones.

### Validation Function

**File:** `promptpotter/services/campaign/campaign_setup.py`

`_validate_local_eval_access(token)` — single validation point, called from `init_services()` before constructing `LLMOnlyAdapter`. Uses `hmac.compare_digest()` for constant-time string comparison.

**Three failure modes with clear messages:**

| Condition | Error |
|-----------|-------|
| `LOCAL_EVAL_SECRET` empty (admin hasn't enabled) | "LLM-only eval requested but local eval is not enabled. Set LOCAL_EVAL_SECRET in .env." |
| No `local_eval_token` provided (user forgot) | "LLM-only eval requested but no local_eval_token provided. Add it to campaign.json." |
| Token doesn't match secret (unauthorized) | "Invalid local_eval_token -- does not match LOCAL_EVAL_SECRET." |

### init_services() Signature

**File:** `promptpotter/services/campaign/campaign_setup.py`

```python
async def init_services(
    backend_url, backend_id, experiment_id,
    project_root, dataset_name,
    dataset_type=None,           # "llm-only" or None
    local_eval_token=None,       # must match LOCAL_EVAL_SECRET
    on_status=None,
) -> BackendContext:
```

When `dataset_type == "llm-only"`:
1. `_validate_local_eval_access(local_eval_token)` — raises `ValueError` on failure
2. `_create_llm_only_client(project_root, dataset_name)` — instantiates `LLMOnlyAdapter` with the optimizer's `LLMClientBase`
3. Status log: `"Backend: llm-only (authorized)"`

Otherwise: `BackendClient(backend_url)` — the default, unchanged path.

### LLMOnlyAdapter

**File:** `promptpotter/services/llm_eval_adapter.py`

Duck-type replacement for `BackendClient`. Implements the subset used by `eval_query_via_backend`:

| Method | Behavior |
|--------|----------|
| `run_query(query, pipeline_params, precomputed)` | Extracts system prompt from `pipeline_params[node]["prompt"]`, calls LLM, returns backend-compatible response dict |
| `check_status()` | Returns `{"status": "ok", "mode": "llm-only"}` |
| `fetch_pipeline()` | Returns minimal pipeline descriptor |
| `init_session(terms)` | No-op (no session needed) |
| `aclose()` | No-op |

The adapter does NOT hold API keys — it receives a pre-instantiated `LLMClientBase`. The system prompt flows through `pipeline_params` via the standard PromptTemplate path: `OptSearchPoint.render()` -> `to_job_search_point()` -> `pipeline_params[node]["prompt"]`.

### Entry Point Threading

Both entry points pass `dataset_type` and `local_eval_token` to `init_services()`:

| Entry Point | File | Source |
|-------------|------|--------|
| CLI | `promptpotter/cli/campaign_runner.py` | `file_config.get("dataset_type")`, `file_config.get("local_eval_token")` |
| Notebook | `promptpotter/ui/campaign/setup.py` | Function parameters |

### Forward Compatibility (Multi-Tenant)

`_validate_local_eval_access()` is the single control point. Future evolution without changing callers:

| Evolution | Change |
|-----------|--------|
| Per-user tokens | Replace `hmac.compare_digest` with DB/KV lookup keyed by token |
| Rate limiting | Wrap the function with a rate limiter keyed on token |
| Audit logging | Add a log line inside the function |
| Token rotation | Admin changes `LOCAL_EVAL_SECRET`; users get new token |
| Role-based access | Check token against a permissions table |

---

## Part 2: TermNorm — `llm_only` Step in /matches

### Design Principle

`llm_only` is a **normal pipeline step** in the existing `/matches` endpoint (research-and-rank). Not a new endpoint. Not an early-exit branch. It follows the same dispatch pattern as every other step: `if "llm_only" in steps`.

When `steps=["llm_only"]`, all other steps (cache_lookup, fuzzy_matching, web_search, entity_profiling, token_matching, llm_ranking) naturally skip because they check `if "their_name" in steps` and find `False`.

### Position in Dispatch Sequence

Last position, after `llm_ranking`:

```
cache_lookup -> fuzzy_matching -> web_search -> entity_profiling -> token_matching -> llm_ranking -> llm_only
```

Rationale: `llm_only` is a complete pipeline replacement — it takes a query and returns an LLM answer directly. Putting it last means all other steps skip naturally when it's the only step. If someone wants `steps=["cache_lookup", "llm_only"]` (check cache first), that composition works.

### Session Validation

`/matches` normally requires an active session with loaded terms. `llm_only` does not need terms. The session precondition is relaxed (not removed):

```python
requires_session = not (set(steps) <= {"llm_only"})
if requires_session:
    # existing session/terms validation
```

This is NOT an early-exit for `llm_only`. It is a precondition relaxation. The rest of the pipeline dispatch runs normally.

### Step Implementation

Inline in the dispatch sequence (same pattern as other steps):

```python
if "llm_only" in steps:
    ctx.set_status("llm_only", "running")
    t0 = time.monotonic()

    node_cfg = params.get("llm_only", {})
    system_prompt = node_cfg.get("prompt", "")
    model = node_cfg.get("model", <pipeline_default>)
    temperature = node_cfg.get("temperature", 0.0)
    max_tokens = node_cfg.get("max_tokens", 2000)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": query})

    llm_response = await llm_call(messages, model, temperature, max_tokens)
    answer = llm_response.content.strip()
    elapsed_ms = round((time.monotonic() - t0) * 1000)

    node_outputs["llm_only"] = {
        "answer": answer,
        "model": llm_response.model,
        "usage": llm_response.usage,
    }
    final_ranking = [{"candidate": answer, "score": 1.0}]
    step_timings["llm_only"] = elapsed_ms
    terminated_at = "llm_only"
    ctx.set_status("llm_only", "completed")
```

### Config Flow

PromptPotter sends `pipeline_params` as `node_config` via `BackendClient.run_query()`. TermNorm's `_resolve_pipeline_params()` merges with `pipeline.json` defaults:

```
PromptPotter pipeline_params              TermNorm pipeline.json defaults
  {"llm_only": {                            {"llm_only": {
    "prompt": "You are a math tutor...",       "model": "openai/gpt-oss-120b",
    "temperature": 0.7                         "temperature": 0.0,
  }}                                           "max_tokens": 2000
                                            }}
           \                               /
            --> _resolve_pipeline_params() -->  merged config
```

The `prompt` field is the primary optimization target — it comes from PromptPotter's `PromptTemplate.render()` -> `JobSearchPoint.pipeline_params`.

### Logging (All Reused, No Changes)

| Log Point | What Happens |
|-----------|-------------|
| Entry | `[PIPELINE] {user_id}: '{query}' (0 terms)` — zero terms signals term-free path |
| Per-step | `ctx.set_status("llm_only", "completed")` — tracked in PipelineContext |
| Exit | `_summarize_response()` — iterates `step_timings`/`final_ranking`, `llm_only` appears naturally |
| Langfuse | `log_pipeline()` — traces `llm_only` as `generation` type |

### Response Format

Standard `/matches` response — no special casing:

```json
{
  "status": "success",
  "message": "Research completed - Found 1 matches in 1.2s",
  "data": {
    "final_ranking": [{"candidate": "42", "score": 1.0}],
    "step_timings": {"llm_only": 1234},
    "node_outputs": {"llm_only": {"answer": "42", "model": "openai/gpt-oss-120b"}},
    "total_time": 1234,
    "terminated_at": "llm_only",
    "pipeline_params": {"steps": ["llm_only"], "llm_only": {"prompt": "..."}},
    "diagnostics": {"warnings": [], "step_statuses": {"llm_only": "success"}}
  }
}
```

### pipeline.json Node Definition

**File:** `backend-api/config/pipeline.json` (TermNorm repo)

```json
"llm_only": {
  "type": "generation",
  "runtime": "backend",
  "node_role": "ranker",
  "description": "Generic LLM call — send a system prompt + user query, get a text response. Bypasses all enrichment (fuzzy, web search, entity profiling). No session or terms required.",
  "config": {
    "model": "openai/gpt-oss-120b",
    "temperature": 0.0,
    "max_tokens": 2000,
    "response_format": "text"
  },
  "optimizer": {
    "param_keys": ["prompt", "model", "temperature", "max_tokens", "response_format"],
    "param_descriptions": {
      "prompt": "System prompt sent to the LLM — the main optimization target",
      "model": "LLM model identifier",
      "temperature": "LLM sampling temperature",
      "max_tokens": "Maximum tokens in LLM response",
      "response_format": "Response mode: 'text' (raw LLM output) or 'json' (structured)"
    },
    "langfuse_type": "generation"
  }
}
```

`llm_only` is NOT in TermNorm's `default` pipeline (which is the full enrichment chain). It is invoked explicitly via `steps=["llm_only"]` from PromptPotter's benchmark dataset configs.

### PromptPotter Dataset pipeline.json

**File:** `datasets/gsm8k/pipeline.json` (PromptPotter repo)

Mirrors the TermNorm node definition. PromptPotter loads this for `PipelineSchema` construction. The `llm_only` node appears with `runtime: "backend"` and the optimizer metadata that drives PromptTemplate decomposition and sensitivity scanning.

---

## Two Evaluation Modes — Summary

| Aspect | Backend-Routed (default) | Local LLM-Only (gated) |
|--------|--------------------------|----------------------|
| **Activation** | `dataset_type` absent or not `"llm-only"` | `dataset_type: "llm-only"` + valid `local_eval_token` |
| **Client** | `BackendClient` -> HTTP to `/matches` | `LLMOnlyAdapter` -> direct LLM call |
| **LLM keys for inference** | Held by backend server | Server's optimizer keys (shared) |
| **Backend required** | Yes | No |
| **Pipeline logging** | Full TermNorm pipeline logging + Langfuse | Minimal (adapter returns backend-compatible dict) |
| **Caching** | `IntermediateCache` + `dataset_run_store` | `dataset_run_store` only |
| **Use case** | Production, multi-tenant, auditable | Development, CI, offline benchmarking |

### When to Use Which

- **Backend-routed**: Default for all production and multi-tenant usage. Full observability, pipeline reuse, key separation. Requires a running TermNorm instance.
- **Local LLM-only**: When no backend is available (CI pipelines, offline development, reviewer reproduction). Requires explicit admin authorization via `LOCAL_EVAL_SECRET`.

---

## Dataset Config Pattern

Each benchmark dataset in `datasets/{name}/` supports both modes:

| File | Backend-Routed Fields | Local LLM-Only Fields |
|------|----------------------|----------------------|
| `campaign.json` | `dataset_name`, `scoring` | + `dataset_type: "llm-only"`, `local_eval_token` (user-provided) |
| `pipeline.json` | `llm_only` node with `runtime: "backend"`, optimizer metadata | Same file — used for schema loading |
| `dataset.md` | Documents backend prerequisites | Documents both modes |

---

## Testing

### PromptPotter

| Test | Verifies |
|------|----------|
| `test_rejects_when_no_secret` | `LOCAL_EVAL_SECRET` empty -> clear "not enabled" error |
| `test_rejects_missing_token` | No `local_eval_token` -> clear "add to campaign.json" error |
| `test_rejects_wrong_token` | Mismatch -> clear "invalid token" error |
| `test_accepts_correct_token` | Matching token -> no error, adapter created |
| `test_run_query_returns_backend_format` | `LLMOnlyAdapter.run_query()` returns standard response dict |
| `test_prompt_from_pipeline_params` | System prompt extracted from `pipeline_params[node]["prompt"]` |

### TermNorm

| Test | Verifies |
|------|----------|
| `/matches` with `steps=["llm_only"]` | Standard response with LLM answer in `final_ranking[0].candidate` |
| Console output | `[PIPELINE]` entry log + `_summarize_response()` exit present |
| Normal pipeline unchanged | `/matches` without `llm_only` -> same behavior |
| Langfuse trace | `llm_only` step traced as `generation` type |
