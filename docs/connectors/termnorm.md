# TermNorm Connector

**Version:** 0.5.0
**Date:** 2026-02-22

Validated connector contract for TermNorm backend integration.

## Connection

| Field | Value |
|-------|-------|
| Base URL | `http://127.0.0.1:8000` (default local) |
| Auth | None (local development) |
| Transport | HTTP/1.1, JSON request/response |
| Timeout | 30s default, 120s for `/matches` |

## Session Lifecycle

TermNorm is stateful — a session must be initialized with terms before calling `/matches`.

```
POST /sessions → POST /matches (×N) → (session expires on server restart)
```

## Endpoints

### `POST /sessions` — Initialize Session

Loads the candidate term list into the server's in-memory matcher.

**Request:**
```json
{
  "terms": ["Stainless Steel Sheet 304", "Aluminum Alloy 6061", ...]
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Session initialized with 847 terms",
  "data": { "term_count": 847 }
}
```

### `POST /matches` — Research & Rank

Full pipeline execution for a single query. Four-stage pipeline:
1. **web_search** — external web search and page scraping to gather research content
2. **entity_profiling** (LLM1) — LLM builds structured entity profile from web content
3. **token_matching** — deterministic token-overlap scoring against session terms
4. **llm_ranking** (LLM2) — LLM reranks token-matched candidates using entity profile

**Request:**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "skip_llm_ranking": false
}
```

Set `skip_llm_ranking: true` to skip stage 4 and return raw token-match scores.

#### Pipeline Step Control (`steps`)

The `steps` parameter controls which pipeline stages execute. When provided, it takes precedence over `skip_llm_ranking`. When absent, the backend falls back to `skip_llm_ranking` (full backward compatibility).

**Request with `steps` (shortened pipeline — no LLM reranking):**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "steps": ["web_search", "entity_profiling", "token_matching"]
}
```

**Request with `steps` (full pipeline):**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "steps": ["web_search", "entity_profiling", "token_matching", "llm_ranking"],
  "ranking_prompt": "You are a domain expert..."
}
```

The response `pipeline_params.steps` echoes the actual steps that ran, regardless of how stage control was specified.

| Step name | Always runs | Description |
|-----------|-------------|-------------|
| `web_search` | Yes | External web search and page scraping |
| `entity_profiling` | Yes | LLM builds structured entity profile |
| `token_matching` | Yes | Deterministic token-overlap scoring |
| `llm_ranking` | No | LLM reranks candidates (controlled by `steps` or `skip_llm_ranking`) |

#### Pipeline Parameter Overrides

PromptPotter forwards `pipeline_params` into the `/matches` request body, giving the optimizer control over every influential knob. All parameters are optional and fall back to their defaults when omitted.

| Parameter | Default | Stage | Description |
|-----------|---------|-------|-------------|
| `max_sites` | `7` | Web scraping | Number of pages fetched and scraped |
| `num_results` | `20` | Web search | Search result count (Brave / SearXNG) |
| `content_char_limit` | `800` | Web scraping | Max characters kept per scraped page |
| `raw_content_limit` | `5000` | Entity profiling | Total research text sent to LLM1 |
| `profiling_temperature` | `0.3` | Entity profiling | LLM1 temperature |
| `profiling_max_tokens` | `1800` | Entity profiling | LLM1 output token limit |
| `ranking_prompt` | (registry default) | LLM ranking | Full LLM2 prompt template override. When provided, replaces the prompt from TermNorm's prompt registry. Used by PromptPotter to inject candidate prompts during optimization. |
| `ranking_temperature` | `0` | LLM ranking | LLM2 temperature |
| `ranking_max_tokens` | `4000` | LLM ranking | LLM2 output token limit |
| `ranking_sample_size` | `20` | LLM ranking | Candidates sampled for LLM2 |
| `max_token_candidates` | `20` | Token matching | Candidates kept from token matching |
| `relevance_weight_core` | `0.7` | Scoring | Weight of core concept score (`spec_score` gets `1 - weight`) |

**Example request with pipeline parameter overrides:**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "skip_llm_ranking": false,
  "max_sites": 3,
  "profiling_temperature": 0.1,
  "ranking_sample_size": 15,
  "relevance_weight_core": 0.8
}
```

**Example request with ranking prompt override (grid search / optimization):**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "skip_llm_ranking": false,
  "ranking_prompt": "You are a domain expert...\n\nGiven the entity profile:\n{{entity_profile_json}}\n\nRank these candidates:\n{{matches}}\n\n..."
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Research completed - Found 20 matches in 14.2s",
  "data": {
    "ranked_candidates": [
      {
        "candidate": "Copper Sheet CW004A",
        "relevance_score": 0.95,
        "core_concept_score": 0.95,
        "spec_score": 0.8,
        "evaluation_reasoning": "...",
        "key_match_factors": ["CW004A", "copper", "sheet"],
        "spec_gaps": []
      }
    ],
    "entity_profile": {
      "core_concept": "sheet",
      "entity_category": "metal sheet product",
      "materials": ["copper", "CW004A"],
      "processes": ["laser cutting"],
      "specifications": ["CW004A"],
      "aliases": ["Kupferblech"],
      "...": "..."
    },
    "token_matched_candidates": [
      ["Copper Sheet CW004A", 0.85],
      ["Copper Plate C10200", 0.62],
      ["...up to 20 entries"]
    ],
    "llm_provider": "groq/meta-llama/llama-4-maverick-17b-128e-instruct",
    "total_time": 14.2,
    "web_search_status": "success",
    "web_search_error": null
  }
}
```

#### Response Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `ranked_candidates` | `list[dict]` | LLM-ranked results (or token-match results if `skip_llm_ranking=true`). Top-1 is the prediction. |
| `entity_profile` | `dict` | Structured profile from web research (LLM1). Contains `core_concept`, `entity_category`, `materials`, `processes`, `specifications`, `aliases`, and more. |
| `token_matched_candidates` | `list[tuple]` | Raw `[term, score]` pairs from deterministic token matching (up to 20). These are the candidates fed to LLM2. |
| `llm_provider` | `string` | Provider/model used for ranking (e.g. `groq/llama-4-maverick`). `null` when `skip_llm_ranking=true`. |
| `total_time` | `float` | Wall-clock seconds for the full pipeline. |
| `web_search_status` | `string` | `"success"` or `"failed"` for the web research step. |
| `web_search_error` | `string\|null` | Error message if web search failed, else `null`. |

#### `skip_llm_ranking` Behavior (legacy)

**Preferred:** Use `steps` to control which stages execute. `skip_llm_ranking` is supported for backward compatibility.

When `true` (or when `steps` omits `"llm_ranking"`), the pipeline stops after token matching. `ranked_candidates` contains raw token-match results formatted as:
```json
{ "candidate": "term", "relevance_score": 0.85, "core_concept_score": 0.85, "spec_score": 0 }
```
`entity_profile` is still computed (web research always runs). `llm_provider` is `null`.

### `GET /pipeline` — Discover Pipeline Schema

Returns the full pipeline topology with typed steps, input/output signatures, and tunable parameter schema. This is the **discovery endpoint** — PromptPotter calls it to learn what the backend's pipeline looks like and which parameters are available for optimization, instead of hardcoding knowledge of the pipeline structure.

**Response:**
```json
{
  "name": "TermNormPipeline",
  "version": "production_v1",
  "steps": [
    {
      "name": "web_search",
      "type": "ExternalService",
      "signature": { "input_fields": ["query"], "output_fields": ["web_content"] },
      "config": {}
    },
    {
      "name": "entity_profiling",
      "type": "LLMGeneration",
      "signature": { "input_fields": ["query", "web_content"], "output_fields": ["entity_profile"] },
      "config": { "model": "...", "temperature": 0.0, "prompt_version": "production_v1" }
    },
    {
      "name": "token_matching",
      "type": "DeterministicFunction",
      "signature": { "input_fields": ["entity_profile"], "output_fields": ["candidates"] },
      "config": { "fuzzy_threshold": 0.7 }
    },
    {
      "name": "llm_ranking",
      "type": "LLMGeneration",
      "signature": { "input_fields": ["entity_profile", "candidates"], "output_fields": ["ranked_list"] },
      "config": { "model": "...", "temperature": 0.0, "prompt_version": "reranker_v1" }
    }
  ],
  "parameters": [
    { "name": "max_sites", "type": "int", "default": 7, "step": "web_search", "description": "Number of pages fetched and scraped" },
    { "name": "num_results", "type": "int", "default": 20, "step": "web_search", "description": "Search result count" },
    { "name": "content_char_limit", "type": "int", "default": 800, "step": "web_search", "description": "Max chars kept per scraped page" },
    { "name": "raw_content_limit", "type": "int", "default": 5000, "step": "entity_profiling", "description": "Total research text sent to LLM1" },
    { "name": "profiling_temperature", "type": "float", "default": 0.3, "step": "entity_profiling", "description": "LLM1 temperature" },
    { "name": "profiling_max_tokens", "type": "int", "default": 1800, "step": "entity_profiling", "description": "LLM1 output token limit" },
    { "name": "ranking_temperature", "type": "float", "default": 0, "step": "llm_ranking", "description": "LLM2 temperature" },
    { "name": "ranking_max_tokens", "type": "int", "default": 4000, "step": "llm_ranking", "description": "LLM2 output token limit" },
    { "name": "ranking_sample_size", "type": "int", "default": 20, "step": "llm_ranking", "description": "Candidates sampled for LLM2" },
    { "name": "ranking_prompt", "type": "string", "default": null, "step": "llm_ranking", "description": "Full LLM2 prompt template override" },
    { "name": "max_token_candidates", "type": "int", "default": 20, "step": "token_matching", "description": "Candidates kept from token matching" },
    { "name": "relevance_weight_core", "type": "float", "default": 0.7, "step": "token_matching", "description": "Weight of core concept score" }
  ]
}
```

**Step types:**

| Type | Description |
|------|-------------|
| `ExternalService` | External API call (web search, scraping) — no LLM involved |
| `LLMGeneration` | LLM inference step with prompt, temperature, and token limits |
| `DeterministicFunction` | Pure computation — same input always produces same output |

**Parameter schema fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Parameter name (matches the key in `/matches` request body) |
| `type` | `string` | Data type: `int`, `float`, `string` |
| `default` | `any` | Default value used when omitted from `/matches` request |
| `step` | `string` | Which pipeline step this parameter controls |
| `description` | `string` | Human-readable description |

### `GET /experiments` — List Experiments

Returns all registered experiments with metadata.

**Response:**
```json
{
  "experiments": [
    {
      "experiment_id": "1_production_historical",
      "name": "Production Historical",
      "description": "...",
      "lifecycle_stage": "production"
    }
  ],
  "total": 1
}
```

### `GET /experiments/{id}/mappings` — Full Experiment Data Package

Returns the complete experiment snapshot used by PromptPotter for sync.

**Response fields:**
- `experiment` — experiment metadata
- `mappings` — ground-truth `[{bom_material, dataset_entry}]` pairs
- `runs` — all runs with:
  - `params`, `metrics`, `tags`
  - `pipeline.config` — step definitions with model/prompt info
  - `pipeline.notation` — shorthand like `LLM1-TokenMatching-LLM2`
  - `evaluation_results` — per-query results from the original run
- `dependencies.prompts` — resolved prompt templates (e.g. `llm_ranking/reranker_v1`)

## Pipeline Architecture

```
query
  │
  ├─► web_search (ExternalService)  ──► web_content (raw scraped pages)
  │                                          │
  │                                          ▼
  ├─► entity_profiling (LLMGeneration)  ◄── web_content
  │         │
  │         ▼
  │   entity_profile (structured JSON)
  │         │
  │         ▼
  ├─► token_matching (DeterministicFunction) ◄── entity_profile search terms
  │         │
  │         ▼
  │   candidate_results  (top 20 by token overlap)
  │         │
  │         ▼
  └─► llm_ranking (LLMGeneration)  ◄── entity_profile + candidates
              │
              ▼
        ranked_candidates  (re-ranked by semantic relevance)
```

The pipeline topology and all tunable parameters are discoverable via `GET /pipeline` — see [endpoint spec](#get-pipeline--discover-pipeline-schema).

## PromptPotter Integration

### Discovery-Driven Pipeline Protocol

PromptPotter uses a three-step discovery protocol to optimize backend pipelines without hardcoding knowledge of their structure:

1. **Discover** — `GET /pipeline` returns the pipeline topology (steps, types, signatures) and tunable parameter schema (names, types, defaults, which step each belongs to). PromptPotter learns what the pipeline looks like and what knobs are available.

2. **Load eval data** — `GET /experiments/{id}/mappings` returns ground-truth query→answer pairs for scoring. (Already exists, no changes needed.)

3. **Generate + evaluate** — The notebook (or API) uses the discovered `parameters` schema to generate new parameter combinations, then sends each configuration one-by-one to `POST /matches` with the parameter overrides. Results are scored against the eval data from step 2.

```
PromptPotter                              TermNorm
    │                                        │
    ├── GET /pipeline ──────────────────────►│
    │◄── pipeline topology + parameter schema│
    │                                        │
    ├── GET /experiments/{id}/mappings ─────►│
    │◄── eval data (query → ground truth)    │
    │                                        │
    │  [generate param combinations from     │
    │   discovered schema]                   │
    │                                        │
    ├── POST /matches { params_1 } ────────►│
    │◄── results_1                           │
    ├── POST /matches { params_2 } ────────►│
    │◄── results_2                           │
    │  ...                                   │
    │                                        │
    │  [score results, select winner]        │
```

This replaces any hardcoded knowledge of TermNorm's pipeline structure (step count, parameter names, defaults) with runtime discovery. New parameters added to TermNorm are automatically available to PromptPotter without code changes.

### Evaluation Mode

All evaluation (grid search and optimization campaigns) runs via the TermNorm backend. The backend runs the **full pipeline** for every query: web search → LLM1 (entity profiling) → token matching (database) → LLM2 (reranking with candidate prompt). PromptPotter passes the candidate prompt via the `ranking_prompt` parameter.

```
PromptPotter grid_search.py
  │
  ├─ POST /sessions (once per grid search run)
  │
  └─ for each combo × query:
       POST /matches { query, steps: [...], ranking_prompt: ps.render() }
         ├─ Web search → entity_profile       (re-runs every time)
         ├─ Token matching against DB          (re-runs every time)
         └─ LLM2 reranking with ranking_prompt (only step that varies)
```

**Cost:** ~10-30s per query (web search + LLM1 + token matching + LLM2). For 35 queries × 34 combos = ~1,190 calls.

**Crash protection:** Incremental writes to `.partial.jsonl` after each query. On restart, resumes from last completed query. Completed combos are cached via content-addressed hashing and skipped on re-run.

### Future Optimization: Cached Intermediates in Grid Search

The backend eval mode is accurate but wasteful: steps 1-3 (web search → LLM1 → token matching) produce identical results for the same query regardless of the ranking prompt. Only LLM2 varies between grid search combos.

Since `/matches` already accepts pre-computed `entity_profile` + `token_matched_candidates`, PromptPotter can cache these after the first full pipeline run and pass them on subsequent calls for the same query:

1. Run full pipeline once per query to get `entity_profile` + candidates (~10-30s × N queries)
2. For each grid combo, call `/matches` with the cached intermediates + candidate prompt (~2s × N queries × M combos)

This reduces grid search time from O(N × M × 20s) to O(N × 20s + N × M × 2s) — a ~10x speedup for typical grid sizes. PromptPotter-side change only.
