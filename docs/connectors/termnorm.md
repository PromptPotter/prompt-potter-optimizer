# TermNorm Connector

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

Full pipeline execution for a single query. Three-stage pipeline:
1. **entity_profiling** (LLM1) — web search + LLM to build structured entity profile
2. **token_matching** — deterministic token-overlap scoring against session terms
3. **llm_ranking** (LLM2) — LLM reranks token-matched candidates using entity profile

**Request:**
```json
{
  "query": "Kupferblech CW004A / Laserschneiden",
  "skip_llm_ranking": false
}
```

Set `skip_llm_ranking: true` to skip stage 3 and return raw token-match scores.

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
| `ranking_temperature` | `0` | LLM ranking | LLM2 temperature |
| `ranking_max_tokens` | `4000` | LLM ranking | LLM2 output token limit |
| `ranking_sample_size` | `20` | LLM ranking | Candidates sampled for LLM2 |
| `max_token_candidates` | `20` | Token matching | Candidates kept from token matching |
| `relevance_weight_core` | `0.7` | Scoring | Weight of core concept score (`spec_score` gets `1 - weight`) |

**Example request with overrides:**
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

#### `skip_llm_ranking` Behavior

When `true`, the pipeline stops after token matching. `ranked_candidates` contains raw token-match results formatted as:
```json
{ "candidate": "term", "relevance_score": 0.85, "core_concept_score": 0.85, "spec_score": 0 }
```
`entity_profile` is still computed (web research always runs). `llm_provider` is `null`.

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
  ├─► entity_profiling (LLM1)  ──► entity_profile (structured JSON)
  │                                       │
  │                                       ▼
  ├─► token_matching ◄── entity_profile search terms
  │         │
  │         ▼
  │   candidate_results  (top 20 by token overlap)
  │         │
  │         ▼
  └─► llm_ranking (LLM2)  ◄── entity_profile + candidates
              │
              ▼
        ranked_candidates  (re-ranked by semantic relevance)
```

## PromptPotter Integration

PromptPotter stores the full `/matches` response in `pipeline_data` on each `ExecutionResultItem`. This captures `entity_profile` and `token_matched_candidates` for local prompt optimization without re-running the TermNorm pipeline.

```python
# Access pipeline intermediates from a stored execution
for result in execution.results:
    profile = result.pipeline_data.get("entity_profile", {})
    candidates = result.pipeline_data.get("token_matched_candidates", [])
```
