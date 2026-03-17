# n8n → PipelineSchema Mapper

**Version:** 1.0.0
**Date:** 2026-02-28
**Status:** Research complete (M6). Implementation deferred to M9.
**Depends on:** [M6 spec](m6-pipeline-composability.md) (PipelineSchema model), [M8 spec](m8-multi-connector.md) (ConnectorProtocol)

---

## 1. Overview

### What

A mapper that converts n8n workflow JSON exports into `PipelineSchema` objects with a structured gap report. Uses the official `@n8n/workflow-sdk` as a Node.js bridge for parsing (optional dependency), falling back to raw JSON traversal when Node.js is unavailable.

### Why

`PipelineSchema` assumes a Python backend exposing `GET /pipeline`. n8n workflows are DAGs of typed nodes with a fundamentally different structure:
- Multiple entry points (webhook chains) in one file
- AI sub-nodes wired via non-`main` connection types (`ai_languageModel`, `ai_outputParser`)
- Opaque Code nodes with parameters embedded in JavaScript source
- No equivalent of Langfuse observation mappings

A mapper bridges this gap, enabling PromptPotter to optimize n8n-hosted pipelines.

### When

- **M6** (this document): Research, architecture decisions, gap analysis
- **M8**: ConnectorProtocol — prerequisite for any non-TermNorm connector
- **M9**: Implementation using this spec as guide

### Real test case

[`docs/connectors/websearch-entity-profiling.n8n.json`](../connectors/websearch-entity-profiling.n8n.json) — a 26-node n8n workflow implementing the TermNorm web search + entity profiling pipeline with 3 webhook endpoints.

---

## 2. n8n Workflow Structure Analysis

Analysis based on the real workflow (`websearch-entity-profiling.n8n.json`, 26 nodes).

### Node type inventory

| Node Type | Count | Examples |
|-----------|:-----:|---------|
| `n8n-nodes-base.webhook` | 3 | GET /workflow, POST /sessions, POST /matches |
| `n8n-nodes-base.code` | 7 | Resolve Config, Store Terms, Parse Query, Cache Lookup, Fuzzy Matching, Candidate Matching, Format Response |
| `n8n-nodes-base.httpRequest` | 2 | n8n API (self-export), Brave Search |
| `n8n-nodes-base.respondToWebhook` | 3 | Respond Workflow, Respond Sessions, Respond Matches |
| `@n8n/n8n-nodes-langchain.chainLlm` | 2 | Entity Profiling LLM, LLM Ranking |
| `@n8n/n8n-nodes-langchain.lmChatGroq` | 2 | Groq Model, Ranking Groq Model |
| `@n8n/n8n-nodes-langchain.outputParserStructured` | 2 | Entity Profile Schema, Ranking Output Schema |
| `n8n-nodes-base.stickyNote` | 5 | Documentation/reference notes |

### Connection types

Three distinct connection types:

1. **`main`** — Standard data flow between pipeline steps
2. **`ai_languageModel`** — LangChain language model input (sub-node → parent)
3. **`ai_outputParser`** — LangChain structured output schema (sub-node → parent)

Connection structure:
```json
{
  "source_node_name": {
    "connection_type": [
      [{ "node": "target_node_name", "type": "connection_type", "index": 0 }]
    ]
  }
}
```

Sub-node connections flow **from** the sub-node **to** its parent. Example: `"Groq Model"` has an `ai_languageModel` connection targeting `"Entity Profiling LLM"`. This is the reverse of the data flow direction — the sub-node _configures_ the parent.

### Webhook chains

Three separate entry points in one file:

| Chain | Trigger | Method | Path | Nodes | Purpose |
|:-----:|---------|--------|------|:-----:|---------|
| 0 | POST /matches | POST | `termnorm/matches` | ~10 | The actual pipeline |
| 1 | GET /workflow | GET | `termnorm/workflow` | 3 | Workflow metadata self-export |
| 2 | POST /sessions | POST | `termnorm/sessions` | 2 | Session initialization |

Only chain 0 is the optimization target. The mapper must isolate it.

### Node parameter structures

**Code nodes** — All 7 Code nodes use identical structure. No `language` field present (defaults to JavaScript). The only meaningful parameter is `jsCode`:

```json
{
  "parameters": { "jsCode": "..." },
  "type": "n8n-nodes-base.code",
  "typeVersion": 2
}
```

**Webhook nodes** — Entry points with `path`, `httpMethod`, `responseMode`:

```json
{
  "parameters": {
    "path": "termnorm/matches",
    "httpMethod": "POST",
    "responseMode": "responseNode",
    "options": {}
  },
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 2
}
```

**httpRequest nodes** — External API calls. Query parameters are nested objects:

```json
{
  "parameters": {
    "url": "https://api.search.brave.com/res/v1/web/search",
    "authentication": "genericCredentialType",
    "genericAuthType": "httpHeaderAuth",
    "sendQuery": true,
    "queryParameters": {
      "parameters": [
        { "name": "q", "value": "={{ $json.bom_material }}" },
        { "name": "count", "value": "={{ $json.num_results }}" }
      ]
    }
  },
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2
}
```

**chainLlm nodes** — LLM invocation with system/user prompts, output parser flag:

```json
{
  "parameters": {
    "promptType": "define",
    "text": "=RESEARCH DATA:\n={{ expression }}...",
    "hasOutputParser": true,
    "messages": {
      "messageValues": [{ "message": "=You are a ..." }]
    },
    "batching": {}
  },
  "type": "@n8n/n8n-nodes-langchain.chainLlm",
  "typeVersion": 1.7,
  "retryOnFail": true,
  "maxTries": 2
}
```

Notable: `retryOnFail` and `maxTries` are top-level fields, not inside `parameters`.

**lmChatGroq nodes** — Model selection:

```json
{
  "parameters": {
    "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
    "options": {}
  },
  "type": "@n8n/n8n-nodes-langchain.lmChatGroq",
  "typeVersion": 1
}
```

**outputParserStructured nodes** — Full JSON Schema v4 embedded as string:

```json
{
  "parameters": {
    "schemaType": "manual",
    "inputSchema": "{\"type\": \"object\", \"properties\": {...}, \"required\": [...]}"
  },
  "type": "@n8n/n8n-nodes-langchain.outputParserStructured",
  "typeVersion": 1.3
}
```

### I/O schema information

Output schemas are defined in `outputParserStructured` nodes as embedded JSON Schema v4 strings. Two schemas exist in the real workflow:

- **Entity Profile Schema**: 11-field object (entity_name, core_concept, entity_category, aliases, professional_classification_aliases, technical_specifications, materials, manufacturing_processes, applications, properties_and_characteristics, standards_and_codes, related_terms)
- **Ranking Output Schema**: Object with `reasoning` (string) and `ranked_candidates` (array of {rank, candidate, relevance_score, rationale})

No input schemas are formally defined. Data flows via n8n expressions:
- `$input.first().json` — Previous node output
- `$json.*` — Current item fields
- `$('Node Name').first().json.*` — Cross-node reference

### Node metadata

Per-node top-level fields: `parameters`, `type`, `typeVersion` (number, e.g., 2, 4.2, 1.7), `position`, `id`, `name`

Optional: `webhookId` (webhook nodes), `retryOnFail` / `maxTries` (chainLlm nodes)

Workflow-level: `meta.instanceId`, `meta.templateCredsSetupCompleted`

---

## 3. n8n TypeScript Ecosystem

Five tools were evaluated for their relevance to the mapper:

| Tool | Maintainer | Description | Relevance |
|------|-----------|-------------|-----------|
| **`@n8n/workflow-sdk`** | n8n-io (official) | 104K-line SDK in n8n monorepo. Semantic graph, code generation, Zod schema generation, workflow builder. | **Highest** — official, comprehensive, directly overlaps with mapper needs |
| **n8n-kit** (`@vahor/n8n-kit`) | Vahor | Define workflows in TypeScript. Auto-generated typed node definitions from n8n repo. JSON↔TS import/export. | High — typed node parameter interfaces could inform Pydantic models, but SDK subsumes this |
| **n8n-mcp** | Romuald Czlonkowski | MCP server with SQLite DB of 1,084 node definitions. 99% property coverage, 2,709 workflow templates. | Medium — reference database for node types, not directly needed |
| **code8n** | Community | One-way n8n JSON → TypeScript monorepo converter. 27 supported node types. Alpha. | Low — alpha, limited, no bidirectional |
| **n8n-skills** | Romuald Czlonkowski | 7 Claude Code skills for n8n workflow building patterns. Educational overlays. | Low — educational, not programmatic |

---

## 4. Architecture Decision: SDK Bridge

### Decision

Use the official `@n8n/workflow-sdk` as the parser via a Node.js bridge subprocess. The bridge script outputs structured JSON, which a thin Python mapper consumes. When Node.js is unavailable, fall back to raw JSON traversal (degraded but functional).

### Why not hand-maintained Pydantic models?

The n8n-kit project generates TypeScript interfaces for every n8n node type. Mirroring these as Pydantic models was considered (Option A) but rejected:

- **Maintenance burden**: n8n releases new node types and updates existing ones regularly. Hand-maintained models go stale.
- **SDK covers validation**: The official SDK already handles type validation and stays current with n8n releases.
- **Scope creep**: Per-node-type models are a full-time maintenance commitment for a single feature.

The user explicitly chose the SDK-delegated approach: _"the main thing we want to avoid is... When the SDK's types update, we update our Pydantic models."_

### Architecture diagram

```
n8n JSON file
     │
     ▼
┌─────────────────────┐
│  Node.js bridge     │  external/n8n-bridge/bridge.mjs
│  @n8n/workflow-sdk  │  buildSemanticGraph() → structured JSON
└─────────┬───────────┘
          │ stdout (JSON)
          ▼
┌─────────────────────┐
│  Python mapper      │  api/services/n8n_mapper.py
│  Pydantic models    │  api/models/n8n_workflow.py (bridge output models)
│  for bridge output  │  NOT per-node-type models
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  PipelineSchema     │  + MappingReport (gaps, extensions)
└─────────────────────┘
```

### Fallback when Node.js unavailable

Raw JSON traversal handles the same workflow format but without SDK validation or semantic graph construction. The mapper walks `connections` and `nodes` arrays directly, building its own adjacency map. Feature parity for linear pipelines; degraded for complex DAGs with control flow.

---

## 5. SDK Internals (Key Components)

### `semantic-graph.ts`

Converts n8n workflow JSON (index-based connections) into a meaningful graph using semantic names.

**SemanticNode** properties:
- `name` — Node identifier (fallback to `id` if name missing)
- `type` — Node type classification
- `json` — Original NodeJSON data (includes `parameters`)
- `outputs` — Map of output names → SemanticConnection arrays
- `inputSources` — Map tracking input source origins
- `subnodes` — Array of AI sub-node connections (language models, parsers, embeddings) with deterministic ordering via index
- `annotations` — Metadata: `isTrigger`, `isCycleTarget`, `isConvergencePoint`

**SemanticConnection**: `target`, `targetInputSlot` (named, not numeric), `outputSlot`

Key functions:
- `buildSemanticGraph()` — Main entry point
- `identifyRoots()` — Locates triggers and nodes without incoming connections
- `parseMainConnections()`, `parseErrorConnections()`, `parseAiConnections()` — Handle respective connection types

### `code-generator.ts`

Generates LLM-friendly SDK code from a composite tree representation.

Processing workflow:
1. Variable registration — pre-registers node names to detect collisions
2. Sub-node collection — gathers AI sub-nodes (languageModel, tools, etc.)
3. Variable declaration generation — `const` declarations for reusable nodes
4. Code generation — converts composite tree to fluent API calls (`.to()`, `.onTrue()`, `.onFalse()`, `.onCase()`, `.onError()`)
5. Flattening — transforms nested structures into `.add()`, `.to()`, `.connect()` calls

Node types handled: `leaf`, `chain`, `ifElse`, `switchCase`, `merge`, `splitInBatches`, `fanOut`, `multiOutput`, `explicitConnections`

### `generate-zod-schemas.ts` / `generate-types.ts`

Auto-generates Zod validation schemas and TypeScript types from n8n node definitions. Located in `packages/@n8n/workflow-sdk/src/generate-types/`. Used alongside `generate-node-defs-cli.ts` (CLI tool).

### Module exports (codegen)

Types: `SemanticGraph`, `SemanticNode`, `SemanticConnection`, `SubnodeConnection`, `CompositeTree`, `CompositeNode`, `CompositeType`, `NodeSemantics`

Functions: `buildSemanticGraph()`, `annotateGraph()`, `buildCompositeTree()`, `generateCode()`, `generateWorkflowCode()`

---

## 6. Mapper Design (5 Phases)

### Phase A: Parse

```
parse_n8n_export(data: dict) -> N8nWorkflow
```

- Handles optional `data["workflow"]` wrapping (some n8n exports nest under `workflow` key)
- Validates via `N8nWorkflow.model_validate(data)` (bridge output model)
- If bridge available: subprocess call to `bridge.mjs`, output is already structured
- If no bridge: raw JSON traversal, build adjacency maps manually
- Log node count and connection count

### Phase B: Isolate Webhook Chains

```python
class WebhookChain(BaseModel):
    trigger_node: str        # webhook node name
    nodes: list[str]         # ordered node names in chain
    http_method: str = "GET" # from webhook params
    path: str = ""           # from webhook params
```

Functions:
- `_resolve_main_connections(workflow) -> dict[str, list[str]]` — Forward adjacency map, `main` type only
- `_resolve_sub_node_connections(workflow) -> dict[str, list[str]]` — Reverse map: parent → [sub-node names] for `ai_*` connections
- `isolate_webhook_chains(workflow) -> list[WebhookChain]` — BFS from each webhook trigger, sorted longest-first. Stops at `respondToWebhook` nodes.

Expected output for real workflow: 3 chains, `chain[0]` = POST /matches (longest, ~10 nodes).

### Phase C: Classify Nodes

```python
class NodeClassification(str, Enum):
    GENERATION = "generation"
    TOOL = "tool"
    CACHE = "cache"
    RETRIEVER = "retriever"
    CODE = "code"          # maps to "tool" in PipelineStep + gap flag
    SUB_NODE = "sub_node"  # merged into parent, not a separate step
    SKIP = "skip"          # webhook, respondToWebhook, stickyNote
```

Tiered heuristic:

| Tier | Rule | Examples |
|:----:|------|---------|
| 1 | Exact type map | `chainLlm` → GENERATION, `httpRequest` → TOOL |
| 2 | Prefix rules | `lmChat*` → SUB_NODE, `outputParser*` → SUB_NODE, `chain*` → GENERATION |
| 3 | Skip set | `webhook`, `respondToWebhook`, `stickyNote` |
| 4 | Code node | `n8n-nodes-base.code` → CODE |
| 5 | Default | Everything else → TOOL |

Sub-nodes (connected via `ai_languageModel` / `ai_outputParser`) are merged into their parent step's metadata, not emitted as separate `PipelineStep` entries.

### Phase D: Map to PipelineSteps

Parameter extraction by node type:

| Source Node Type | `param_keys` extraction | Notes |
|-----------------|-------------------------|-------|
| `httpRequest` | `queryParameters.parameters[].name` + `url` | Nested structure requires digging |
| `chainLlm` + sub-nodes | `model` (from lmChatGroq), `prompt` (presence), `output_schema` (from outputParserStructured) | Sub-nodes contribute params to parent |
| `code` | `{}` (empty) | Always flagged as `code_node_opaque` gap |

Step name normalization: `"Parse Query & Extract Params"` → `"parse_query_extract_params"` (strip non-alnum except spaces, replace spaces with `_`, lowercase). Dedup with `_2` suffix on collision.

All steps: `runtime="backend"`, `observation_name=None`, `observation_mappings=[]`, `short_circuit=False`.

### Phase E: Assemble PipelineSchema + MappingReport

```
map_n8n_to_pipeline(data: dict, chain_index: int | None = None)
    -> tuple[PipelineSchema, MappingReport]
```

Calls Phase A → D in sequence, then:

```python
schema = PipelineSchema(
    name=workflow.workflow_name or "n8n_workflow",
    version="0.1.0",
    description=f"Mapped from n8n workflow ({len(steps)} steps)",
    steps=steps,
)
```

---

## 7. Gap Analysis

Eight gap categories identified from the real workflow:

| # | Category | Severity | Trigger Condition | Real Example |
|---|----------|----------|-------------------|--------------|
| 1 | `multiple_chains` | info | >1 webhook chain in file | 3 chains: /workflow, /sessions, /matches |
| 2 | `code_node_opaque` | warning | Code node can't be auto-classified | 5 of 8 pipeline nodes are Code — "Cache Lookup (STUB)", "Candidate Matching", etc. |
| 3 | `params_in_code` | warning | Tunable params embedded in JS, not in n8n config | "Parse Query" has all 12 params (max_sites, ranking_temperature, etc.) in `jsCode` |
| 4 | `sub_node_wiring` | info | `ai_languageModel` / `ai_outputParser` connections | Groq Model → Entity Profiling LLM |
| 5 | `observations` | warning | No n8n equivalent for obs mappings | All steps lack observation metadata — n8n has no Langfuse equivalent |
| 6 | `io_boundary` | info | Webhook trigger + respond nodes frame pipeline | POST /matches → Respond Matches |
| 7 | `type_vocabulary` | warning | `"code"` type missing from PipelineStep.type | 5 of 8 pipeline nodes are Code — only "tool", "cache", "retriever", "generation" exist in current vocabulary |
| 8 | `param_schema` | info | Flat `param_keys` can't describe nested/typed params | httpRequest `queryParameters` are nested objects with name/value pairs |

### Gap model

```python
class GapCategory(str, Enum):
    MULTIPLE_CHAINS = "multiple_chains"
    CODE_NODE_OPAQUE = "code_node_opaque"
    PARAMS_IN_CODE = "params_in_code"
    SUB_NODE_WIRING = "sub_node_wiring"
    OBSERVATIONS = "observations"
    IO_BOUNDARY = "io_boundary"
    TYPE_VOCABULARY = "type_vocabulary"
    PARAM_SCHEMA = "param_schema"

class MappingGap(BaseModel):
    category: GapCategory
    node_name: str
    message: str
    severity: str  # "info" | "warning"
```

---

## 8. PipelineSchema Extension Suggestions

The mapper should NOT modify `PipelineSchema` directly. Instead, extension suggestions are surfaced in `MappingReport.extension_suggestions` for human review.

### Identified extension candidates

| Extension | Source | Decision |
|-----------|--------|----------|
| **`io_schema`** | JSON Schemas from `outputParserStructured` nodes | Surface in MappingReport as `SchemaExtension` with `category="io_schema"`. Do NOT add to PipelineStep — cross-cutting M6/M8 decision. |
| **Step-level I/O contracts** | `$('Node Name').first().json.field` expressions reveal data-flow dependencies | PipelineSchema currently has no concept of step-level I/O. Future consideration. |
| **`"code"` step type** | 5 of 8 pipeline nodes are Code | Map to `"tool"` with gap flag. Don't add new PipelineStep type until gap frequency justifies it. |
| **Nested param descriptions** | `param_keys` is `set[str]`, can't describe typed/nested params | httpRequest queryParameters are objects. Future: consider `param_schema: dict` alongside flat `param_keys`. |

### Extension model

```python
class SchemaExtension(BaseModel):
    category: str       # e.g. "io_schema"
    node_name: str
    data: dict          # the JSON schema or other structured data
    message: str
```

---

## 9. Expected Mapper Output

When run against `websearch-entity-profiling.n8n.json` with `chain_index=0` (POST /matches):

### Pipeline steps (8)

| # | Step Name | Type | Source Node | `param_keys` |
|---|-----------|------|-------------|-------------|
| 1 | `parse_query_extract_params` | tool | Code | `{}` |
| 2 | `cache_lookup_stub` | tool | Code | `{}` |
| 3 | `fuzzy_matching_stub` | tool | Code | `{}` |
| 4 | `brave_search` | tool | httpRequest | `{url, q, count}` |
| 5 | `entity_profiling_llm` | generation | chainLlm | `{model, prompt, output_schema}` |
| 6 | `candidate_matching` | tool | Code | `{}` |
| 7 | `llm_ranking` | generation | chainLlm | `{model, prompt, output_schema}` |
| 8 | `format_response` | tool | Code | `{}` |

### Skipped nodes

- POST /matches (webhook trigger) — `SKIP`
- Respond Matches (respondToWebhook) — `SKIP`

### Sub-node merges

| Sub-node | Parent | Connection Type |
|----------|--------|----------------|
| Groq Model | Entity Profiling LLM | `ai_languageModel` |
| Entity Profile Schema | Entity Profiling LLM | `ai_outputParser` |
| Ranking Groq Model | LLM Ranking | `ai_languageModel` |
| Ranking Output Schema | LLM Ranking | `ai_outputParser` |

### Gaps produced

- `multiple_chains` (info): 3 chains found, mapped chain 0
- `code_node_opaque` x5 (warning): Parse Query, Cache Lookup, Fuzzy Matching, Candidate Matching, Format Response
- `params_in_code` (warning): Parse Query has 12+ tunable params in jsCode
- `observations` (warning): No observation mappings assigned
- `type_vocabulary` (warning): 5 Code nodes mapped to "tool" — "code" type not in vocabulary

### Extension suggestions

- `io_schema` for Entity Profile Schema: 11-field object schema (entity_name, core_concept, ...)
- `io_schema` for Ranking Output Schema: ranked_candidates array schema (rank, candidate, relevance_score, rationale)

---

## 10. MappingReport Model

```python
class MappingReport(BaseModel):
    workflow_name: str
    total_nodes: int
    mapped_nodes: int
    skipped_nodes: int
    unmapped_nodes: int
    chains_found: int
    selected_chain: int
    node_classifications: list[ClassifiedNode]
    sub_node_merges: list[SubNodeInfo]
    gaps: list[MappingGap]
    extension_suggestions: list[SchemaExtension]
    topology_is_linear: bool
    has_control_flow: bool

    def summary(self) -> str:
        """Human-readable summary of mapping results."""
        ...
```

Supporting models:

```python
class ClassifiedNode(BaseModel):
    node_name: str
    node_type: str        # n8n type string
    classification: NodeClassification
    step_name: str | None = None  # normalized name if mapped to a step

class SubNodeInfo(BaseModel):
    node_name: str
    node_type: str
    parent_name: str
    connection_type: str  # "ai_languageModel", "ai_outputParser"
    extracted: dict       # params extracted from sub-node (model name, schema, etc.)
```

---

## 11. Bridge Design

### Bridge script (`external/n8n-bridge/bridge.mjs`)

```javascript
import { buildSemanticGraph } from '@n8n/workflow-sdk';

const workflowJson = JSON.parse(readInput());
const graph = buildSemanticGraph(workflowJson);

const output = {
  workflow_name: workflowJson.name,
  nodes: Array.from(graph.nodes.values()).map(node => ({
    name: node.name,
    type: node.json.type,
    parameters: node.json.parameters,
    is_trigger: node.annotations.isTrigger,
    outputs: Object.fromEntries(
      Object.entries(node.outputs).map(([key, conns]) => [
        key,
        conns.map(c => ({ target: c.target, slot: c.targetInputSlot }))
      ])
    ),
    subnodes: node.subnodes.map(s => ({
      name: s.name,
      connection_type: s.type,
      node_type: graph.nodes.get(s.name)?.json.type,
      parameters: graph.nodes.get(s.name)?.json.parameters,
    })),
  })),
  roots: identifyRoots(graph),
};

console.log(JSON.stringify(output));
```

### Pydantic models for bridge output (`api/models/n8n_workflow.py`)

These model the bridge script's **output format**, NOT n8n node parameter types. Parameters stay `dict[str, Any]`.

```python
class N8nSubNode(BaseModel):
    model_config = {"frozen": True}
    name: str
    connection_type: str       # "ai_languageModel", "ai_outputParser"
    node_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)

class N8nConnection(BaseModel):
    model_config = {"frozen": True}
    target: str
    slot: str = "main"

class N8nNode(BaseModel):
    model_config = {"frozen": True}
    name: str
    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_trigger: bool = False
    outputs: dict[str, list[N8nConnection]] = Field(default_factory=dict)
    subnodes: list[N8nSubNode] = Field(default_factory=list)

    @property
    def base_type(self) -> str:
        return self.type.rsplit(".", 1)[-1] if "." in self.type else self.type

class N8nWorkflow(BaseModel):
    model_config = {"frozen": True}
    workflow_name: str = ""
    nodes: list[N8nNode] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
```

### Node.js dependency assessment

| Concern | Assessment |
|---------|-----------|
| Security | `@n8n/workflow-sdk` from official n8n org — reputable. Subprocess isolation limits attack surface. |
| Docker image size | +200-400MB for Node.js runtime |
| Operational overhead | Two package managers (pip + npm), CI needs `npm install` step |
| Feature isolation | n8n connector is completely optional. No Node.js = feature unavailable, clear error message. |

---

## 12. M9 Deliverables

### Files to create

| File | Purpose |
|------|---------|
| `external/n8n-bridge/package.json` | Node.js bridge package (minimal, `@n8n/workflow-sdk` dependency) |
| `external/n8n-bridge/bridge.mjs` | Bridge script: SDK → structured JSON via `buildSemanticGraph()` |
| `api/models/n8n_workflow.py` | Pydantic models for bridge output (NOT per-node-type models) |
| `api/services/n8n_mapper.py` | Python mapper + MappingReport + gap detection (Phases A-E) |
| `tests/test_n8n_mapper.py` | Tests against real fixture (`websearch-entity-profiling.n8n.json`) |

### Files to modify

| File | Change |
|------|--------|
| `api/services/pipeline_discovery.py` | Add `parse_n8n_workflow()` alongside existing `parse_pipeline_response()` |
| `api/models/__init__.py` | Re-export n8n workflow models |

### Test plan

1. **Unit: bridge output parsing** — Validate `N8nWorkflow.model_validate()` against bridge JSON fixture
2. **Unit: webhook chain isolation** — Verify 3 chains extracted, chain 0 is POST /matches with ~10 nodes
3. **Unit: node classification** — Each of the 26 nodes classified correctly per tiered heuristic
4. **Unit: parameter extraction** — httpRequest gets `{url, q, count}`, chainLlm gets `{model, prompt, output_schema}`, code gets `{}`
5. **Unit: step name normalization** — "Parse Query & Extract Params" → "parse_query_extract_params"
6. **Integration: full mapper** — `map_n8n_to_pipeline()` against real workflow fixture → verify 8 steps, correct types, gaps
7. **Fallback: raw JSON** — Same tests without bridge (raw traversal path)

### Verification steps

1. Spec document internally consistent with `PipelineSchema` model (`api/models/pipeline_schema.py`)
2. All 8 gap categories have real examples from the test workflow
3. Bridge output models match SDK's `SemanticGraph` output format
4. MappingReport captures sufficient information for human review

---

## Appendix A: n8n TypeScript Typed Parameters (Reference)

These are the n8n-kit-generated TypeScript interfaces for the node types in the real workflow. Provided as reference for understanding parameter structures — NOT to be mirrored as Pydantic models (see Section 4).

### CodeNodeParameters

```typescript
export interface CodeNodeParameters {
  readonly mode?: "runOnceForAllItems" | "runOnceForEachItem"
  readonly language?: "javaScript" | "pythonNative"
  readonly jsCode?: string
  readonly pythonCode?: string
}
```

### WebhookNodeParameters

```typescript
export interface WebhookNodeParameters {
  readonly httpMethod?: "DELETE" | "GET" | "HEAD" | "PATCH" | "POST" | "PUT"
  readonly path?: string
  readonly authentication?: "basicAuth" | "headerAuth" | "jwtAuth" | "none"
  readonly responseMode?: "onReceived" | "lastNode" | "responseNode" | "streaming"
  readonly responseCode?: number
  readonly responseData?: "allEntries" | "firstEntryJson" | "firstEntryBinary" | "noData"
  readonly options?: { ... }
}
```

### HttpRequestV3NodeParameters

```typescript
export interface HttpRequestV3NodeParameters {
  readonly method?: "DELETE" | "GET" | "HEAD" | "OPTIONS" | "PATCH" | "POST" | "PUT"
  readonly url?: string
  readonly authentication?: "none" | "predefinedCredentialType" | "genericCredentialType"
  readonly sendQuery?: boolean
  readonly queryParameters?: readonly { name: string; value: string }[]
  readonly sendBody?: boolean
  readonly contentType?: "json" | "form-urlencoded" | "multipart-form-data" | "binaryData" | "raw"
  readonly options?: { ... }
}
```

### ChainLlmNodeParameters

```typescript
export interface ChainLlmNodeParameters {
  readonly promptType?: "auto" | "guardrails" | "define"
  readonly text?: string
  readonly hasOutputParser?: boolean
  readonly messages?: readonly { messageType: string; ... }[]
  readonly batching?: { batchSize?: number; delayBetweenBatches?: number }
}
```

### LmChatGroqNodeParameters

```typescript
export interface LmChatGroqNodeParameters {
  readonly model?: string
  readonly options?: { maxTokensToSample?: number; temperature?: number }
}
```

### OutputParserStructuredNodeParameters

```typescript
export interface OutputParserStructuredNodeParameters {
  readonly schemaType?: "fromJson" | "manual"
  readonly inputSchema?: string
  readonly jsonSchema?: string
  readonly autoFix?: boolean
}
```

---

## Appendix B: n8n Platform Notes (Feb 2026)

- **Native TypeScript in Code nodes**: NOT available. Code nodes support JavaScript and Python only.
- **n8n v2**: "Hardening release" — emphasis on security and stability, not new features.
- **No announced changes** to workflow JSON export format, code node language support, or pipeline schema structure.
- **TypeScript requested** by community since May 2025, remains unimplemented natively. Community Deno Code node (`n8n-nodes-deno-code`) supports TS but is third-party.
