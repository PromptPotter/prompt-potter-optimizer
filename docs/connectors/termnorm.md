# TermNorm Connector

**Version:** 0.7.0
**Date:** 2026-03-04

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

**Request (shortened pipeline — no LLM reranking):**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "steps": ["web_search", "entity_profiling", "token_matching"]
}
```

**Request (full pipeline):**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "steps": ["web_search", "entity_profiling", "token_matching", "llm_ranking"],
  "ranking_prompt": "You are a domain expert..."
}
```

The response `pipeline_params.steps` echoes the actual steps that ran.

| Step name | Always runs | Description |
|-----------|-------------|-------------|
| `web_search` | Yes | External web search and page scraping |
| `entity_profiling` | Yes | LLM builds structured entity profile |
| `token_matching` | Yes | Deterministic token-overlap scoring |
| `llm_ranking` | No | LLM reranks candidates (controlled by `steps`) |

#### Pipeline Node Configuration (`node_config`)

PromptPotter uses **`node_config`** format throughout — the same nested dict shape as `pipeline.json` and the `/matches` wire format. `run_match()` forwards `node_config` as-is. No translation layer.

**Example request:**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "steps": ["web_search", "entity_profiling", "token_matching", "llm_ranking"],
  "node_config": {
    "entity_profiling": {
      "prompt": "Custom profiling prompt with {{query}} {{format_string}} {{combined_text}}",
      "temperature": 0.5,
      "max_tokens": 2000,
      "model": "gpt-4o"
    },
    "llm_ranking": {
      "prompt": "Custom ranking prompt with {{core_concept}} {{entity_profile_json}} {{matches}}",
      "temperature": 0.0,
      "sample_size": 15,
      "model": "gpt-4o"
    },
    "web_search": {
      "max_sites": 3,
      "num_results": 10,
      "query_suffix": "material datasheet"
    }
  }
}
```

**`node_config` key mapping:**

| Node | Config key |
|------|-----------|
| `entity_profiling` | `prompt` |
| `entity_profiling` | `output_schema` |
| `entity_profiling` | `model` |
| `entity_profiling` | `temperature` |
| `entity_profiling` | `max_tokens` |
| `entity_profiling` | `raw_content_limit` |
| `llm_ranking` | `prompt` |
| `llm_ranking` | `output_schema` |
| `llm_ranking` | `model` |
| `llm_ranking` | `temperature` |
| `llm_ranking` | `max_tokens` |
| `llm_ranking` | `sample_size` |
| `web_search` | `max_sites` |
| `web_search` | `num_results` |
| `web_search` | `content_char_limit` |
| `web_search` | `query_prefix` |
| `web_search` | `query_suffix` |
| `fuzzy_matching` | `threshold` |
| `fuzzy_matching` | `scorer` |
| `token_matching` | `max_token_candidates` |
| `token_matching` | `relevance_weight_core` |

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
| `ranked_candidates` | `list[dict]` | LLM-ranked results (or token-match results if `llm_ranking` not in `steps`). Top-1 is the prediction. |
| `entity_profile` | `dict` | Structured profile from web research (LLM1). Contains `core_concept`, `entity_category`, `materials`, `processes`, `specifications`, `aliases`, and more. |
| `token_matched_candidates` | `list[tuple]` | Raw `[term, score]` pairs from deterministic token matching (up to 20). These are the candidates fed to LLM2. |
| `llm_provider` | `string` | Provider/model used for ranking (e.g. `groq/llama-4-maverick`). `null` when `llm_ranking` not in `steps`. |
| `total_time` | `float` | Wall-clock seconds for the full pipeline. |
| `web_search_status` | `string` | `"success"` or `"failed"` for the web research step. |
| `web_search_error` | `string\|null` | Error message if web search failed, else `null`. |
| `step_timings` | `dict[str, float\|null]` | Per-step wall-clock seconds in pipeline execution order. `null` for skipped steps. |
| `terminated_at` | `string\|null` | Name of the last pipeline step that produced the final result (e.g. `"llm_ranking"`, `"token_matching"`). |
| `pipeline_params` | `dict` | Effective parameter snapshot for the pipeline run (merged defaults + overrides). |

When `steps` omits `"llm_ranking"`, the pipeline stops after token matching. `ranked_candidates` contains raw token-match results formatted as:
```json
{ "candidate": "term", "relevance_score": 0.85, "core_concept_score": 0.85, "spec_score": 0 }
```
`entity_profile` is still computed (if `entity_profiling` is in `steps`). `llm_provider` is `null`.

### `GET /pipeline` — Discover Pipeline Schema

Returns the full pipeline configuration with typed nodes, tunable parameters, named pipeline variants, and **resolved registry metadata** (schemas + prompts). This is the **discovery endpoint** — PromptPotter calls it to learn what the backend's pipeline looks like and which parameters, schemas, and prompts are available, instead of hardcoding knowledge of the pipeline structure.

**Response (abbreviated):**
```json
{
  "name": "TermNorm",
  "version": "v1.1",
  "nodes": {
    "fuzzy_matching": {
      "type": "DeterministicFunction",
      "short_circuit": true,
      "config": { "threshold": 70, "scorer": "WRatio", "limit": 5 }
    },
    "web_search": {
      "type": "ExternalService",
      "config": { "max_sites": 7, "num_results": 20, "content_char_limit": 800, "query_prefix": "", "query_suffix": "", "..." : "..." }
    },
    "entity_profiling": {
      "type": "LLMGeneration",
      "config": {
        "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "temperature": 0.3, "max_tokens": 1800, "output_format": "json",
        "prompt_family": "entity_profiling", "prompt_version": 1,
        "schema_family": "entity_profile", "schema_version": 1
      }
    },
    "token_matching": {
      "type": "DeterministicFunction",
      "config": { "max_token_candidates": 20, "relevance_weight_core": 0.7, "..." : "..." }
    },
    "llm_ranking": {
      "type": "LLMGeneration",
      "config": {
        "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "temperature": 0.0, "max_tokens": 4000, "ranking_sample_size": 20,
        "prompt_family": "llm_ranking", "prompt_version": 1,
        "schema_family": "llm_ranking_output", "schema_version": 1
      }
    }
  },
  "pipelines": {
    "default": ["web_search", "entity_profiling", "token_matching", "llm_ranking"],
    "with_fuzzy": ["fuzzy_matching", "web_search", "entity_profiling", "token_matching", "llm_ranking"],
    "fuzzy_only": ["fuzzy_matching"]
  },
  "llm_defaults": { "provider": "groq", "model": "...", "timeout": 60, "retry_attempts": 3 },
  "resolved_schemas": {
    "entity_profile/1": {
      "family": "entity_profile", "version": 1,
      "description": "Entity profile extraction schema for web research pipeline",
      "fields": ["entity_name", "core_concept", "distinguishing_features", "..."],
      "json_schema": { "type": "object", "properties": { "...": "..." }, "required": ["..."] }
    },
    "llm_ranking_output/1": {
      "family": "llm_ranking_output", "version": 1,
      "description": "LLM ranking step output schema",
      "fields": ["profile_summary", "core_concept_description", "ranked_candidates"],
      "json_schema": { "...": "..." }
    }
  },
  "resolved_prompts": {
    "entity_profiling/1": {
      "family": "entity_profiling", "version": 1,
      "description": "Extract comprehensive entity profile from web research data",
      "template_variables": ["query", "format_string", "combined_text"],
      "template": "You are a comprehensive technical database API..."
    },
    "llm_ranking/1": {
      "family": "llm_ranking", "version": 1,
      "description": "Rank candidate matches based on entity profile relevance",
      "template_variables": ["core_concept", "entity_profile_json", "matches"],
      "template": "You are a candidate evaluation expert..."
    }
  }
}
```

**Node types:**

| Type | Description |
|------|-------------|
| `ExternalService` | External API call (web search, scraping) — no LLM involved |
| `LLMGeneration` | LLM inference step. Carries `prompt_family`/`prompt_version` and optionally `schema_family`/`schema_version` references into registries. |
| `DeterministicFunction` | Pure computation — same input always produces same output. May have `short_circuit: true` (stops pipeline on hit). |

**Top-level response sections:**

| Section | Description |
|---------|-------------|
| `nodes` | Node definitions with type, config, and optional `short_circuit` flag |
| `pipelines` | Named pipeline variants — ordered lists of node names to execute |
| `llm_defaults` | Default LLM provider, model, timeout, retry settings |
| `resolved_schemas` | Output schemas resolved from the schema registry, keyed by `{family}/{version}` |
| `resolved_prompts` | Prompt templates resolved from the prompt registry, keyed by `{family}/{version}` |

#### Registry Metadata Resolution

LLMGeneration nodes reference schemas and prompts by family + version (e.g. `schema_family: "entity_profile"`, `schema_version: 1`). The `GET /pipeline` handler resolves these references from TermNorm's on-disk registries via `_enrich_with_registries()`:

- **Schema registry** (`logs/schemas/{family}/{version}/`): `schema.json` (full JSON schema) + `metadata.json` (family, version, description, fields)
- **Prompt registry** (`logs/prompts/{family}/{version}/`): `prompt.txt` (template) + `metadata.json` (family, version, template_variables, description)

Resolved artifacts appear in `resolved_schemas` and `resolved_prompts` as top-level dicts. Node configs are NOT modified — they keep the `schema_family`/`prompt_family` references. Missing registrations are silently skipped (debug logged).

**Key principle:** Registry defaults are committed to git (not runtime-initialized). TermNorm owns these artifacts; PromptPotter consumes them from the live response and never hardcodes them.

### `GET /status` — Server Status

Returns current server state for external tools. Aggregates session, match database, experiment, and pipeline info.

**Response:**
```json
{
  "status": "success",
  "data": {
    "session_active": true,
    "active_sessions": 1,
    "terms_loaded": 847,
    "match_database_identifiers": 1200,
    "match_database_aliases": 3500,
    "experiments_count": 2,
    "mappings_count": 150,
    "experiments": [
      { "id": "1_production_historical", "mappings": 100 },
      { "id": "2_evaluation_run", "mappings": 50 }
    ],
    "pipeline_version": "v1.1",
    "llm_provider": "groq",
    "llm_model": "meta-llama/llama-4-maverick-17b-128e-instruct"
  }
}
```

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

PromptPotter uses a discovery-driven protocol to optimize backend pipelines without hardcoding knowledge of their structure:

1. **Discover** — `GET /pipeline` returns the pipeline topology (nodes, types, configs), tunable parameters, and **resolved registry metadata** (output schemas, prompt templates). `parse_pipeline_response()` merges this live metadata onto the known pipeline schema — live always wins over any static defaults.

2. **Load eval data** — `GET /experiments/{id}/mappings` returns ground-truth query→answer pairs for scoring.

3. **Filter axes** — `filter_variant_library()` drops optimization axes not owned by active pipeline steps (e.g. drops `prompt_fields` when `llm_ranking` is inactive). The scan advisor uses the enriched schema (output schema fields + prompt metadata) to recommend which axes to explore.

4. **Generate + evaluate** — The notebook uses the discovered schema to generate parameter combinations, then sends each to `POST /matches` with overrides. Results are scored against eval data.

```
PromptPotter                              TermNorm
    │                                        │
    ├── GET /pipeline ──────────────────────►│
    │◄── nodes + resolved_schemas/prompts    │
    │                                        │
    │  [parse_pipeline_response() merges     │
    │   live metadata onto PipelineSchema]   │
    │                                        │
    │  [filter_variant_library() drops       │
    │   axes not in active pipeline]         │
    │                                        │
    ├── GET /experiments/{id}/mappings ─────►│
    │◄── eval data (query → ground truth)    │
    │                                        │
    ├── POST /matches { params_1 } ────────►│
    │◄── results_1                           │
    ├── POST /matches { params_2 } ────────►│
    │◄── results_2                           │
    │  ...                                   │
    │                                        │
    │  [score results, select winner]        │
```

This replaces any hardcoded knowledge of TermNorm's pipeline structure with runtime discovery. New parameters, schemas, or prompts added to TermNorm are automatically available to PromptPotter without code changes.

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
