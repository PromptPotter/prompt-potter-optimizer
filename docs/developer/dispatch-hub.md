# Dispatch hub

Visual + reference for `promptpotter/application/optimization/dispatch/hub/` — the registry that fills `{{placeholders}}` in the four optimizer prompts. Pairs with [`l1-generate-surface.md`](l1-generate-surface.md) (L1_GENERATE's layout surface) and [`l2-internals.md`](l2-internals.md) (L2_CONTEXT firing).

The hub is stateless. `INJECTIONS` is a typed `dict[str, _Injection]` — each entry carries `name`, `kind` (MEASUREMENT / DERIVED / TRACE / DIRECTIVE), `render: InjectionBundle → str`, and a docstring. `validate_template()` (called from `load_optimizer_prompt`) raises on `{{slot}}` names not in the registry: a typo in a template fails at module load, not at first render.

## Flow

Inputs (left) fill placeholders in optimizer process nodes (right).

- **Amber pill** — optimizer process node: the four LLM prompts (L1_GENERATE, L1_CRITIQUE, L2_CONTEXT, L3_PLAN) plus L1_SCORE (deterministic).
- **Solid orange** — deterministic input (schema, measurements, counters, constants).
- **Default node** — AI-generated input (content originates from another LLM stage).
- **Red arrow** — LLM-produced edge: the feedback loops that close the optimizer. L1_SCORE outputs (`diagnostics`, `l1_wounds`) are computed, so they draw in normal color. `guard_breaches` (post-parse validators on L2's / L3's own output) is LLM-produced, so its producer edges are red.

```mermaid
flowchart LR
  classDef det fill:#FFA500,stroke:#cc7a00,color:#000
  classDef proc fill:#fff3d6,stroke:#f59e0b,stroke-width:4px,color:#000

  %% Standalone AI-generated inputs (not produced inside a single LLM stage)
  CPAR["l1_overrides<br/>• n_variants<br/>• temp"]

  %% L1_SCORE readouts — diagnostics is per-round on Bundle.digest; l1_wounds
  %% (validation parse-time + runtime mid-eval) accumulates on OptSearchPoint cross-round.
  subgraph SR["L1_SCORE readouts"]
    DIAG["diagnostics⁴<br/>• STATUS: round, current, best, stalls<br/>• trajectory + evolution<br/>• rank dist, anomalies, near-misses<br/>• pipeline health, probe outcome"]:::det
    L1W["l1_wounds⁵<br/>• validation (parse-time) + runtime (mid-eval)<br/>• fenced; owner-tagged (l1 | operator)"]:::det
  end
  style SR fill:none,stroke:#888,stroke-dasharray: 5 5

  %% Post-parse guard breaches — produced by L2P / L3P; both owner=L3 (replan).
  GB[guard_breaches¹²]

  %% Loop-Settings — read-only loop-time constants
  subgraph LS["Loop-Settings"]
    TUN[pipeline_param_catalogue⁹]:::det
    CAT[l1_signal_catalogue¹]:::det
  end
  style LS fill:none,stroke:#888,stroke-dasharray: 5 5

  %% L1_GENERATE + L1_SCORE + L2_CONTEXT — standalone process nodes
  L1G([L1_GENERATE]):::proc
  L1S([L1_SCORE]):::proc
  L2P([L2_CONTEXT]):::proc

  %% Standalone L2-produced signals
  TC[task_context³]
  LAYOUT[l1_layout⁷]

  %% Cross-round AxisIndex digest
  AXM[axis_memory¹⁴]:::det

  %% L1_CRITIQUE + its produced injection
  subgraph L1CG[" "]
    L1C([L1_CRITIQUE]):::proc
    CRIT[critique⁸]
  end
  style L1CG fill:none,stroke:none

  %% L3_PLAN + its outputs
  subgraph L3G[" "]
    L3P([L3_PLAN]):::proc
    PLAN[plan]
    L3N[l3_to_l2_note]
  end
  style L3G fill:none,stroke:none

  %% L1_GENERATE inputs — sees its own wounds (l1_wounds). LAYOUT is structural.
  PLAN --> L1G
  TC --> L1G
  TUN --> L1G
  DIAG --> L1G
  L1W --> L1G
  CRIT --> L1G
  CPAR --> L1G
  LAYOUT --> L1G
  AXM --> L1G

  %% L1_CRITIQUE inputs
  PLAN --> L1C
  TC --> L1C
  DIAG --> L1C
  L1W --> L1C

  %% L2_CONTEXT inputs — its framing wounds are guard_breaches; L1's own wounds
  %% are L1's to heal now (the old L2-briefs-L1 path is cut).
  PLAN --> L2P
  L3N --> L2P
  DIAG --> L2P
  GB --> L2P
  CRIT --> L2P
  CPAR --> L2P
  TC --> L2P
  CAT --> L2P
  AXM --> L2P

  %% L3_PLAN inputs — sink: sees both wound groups.
  PLAN --> L3P
  TC --> L3P
  DIAG --> L3P
  L1W --> L3P
  GB --> L3P
  CRIT --> L3P
  AXM --> L3P

  %% LLM-produced feedback edges (red)
  L3P --> PLAN
  L3P --> L3N
  L2P --> TC
  L2P --> CPAR
  L2P --> LAYOUT
  L1C --> CRIT
  L2P --> GB
  L3P --> GB

  %% L1_SCORE derivations — deterministic; normal color
  L1S --> DIAG
  L1S --> L1W

  %% Red styling for the 8 LLM-produced feedback edges (post-round)
  linkStyle 29,30,31,32,33,34,35,36 stroke:#B22222,stroke-width:2px
```

## Reference

13 injections + 1 structural input + 1 caller extra, grouped by role. Numbered items map to the diagram superscripts. `[fenced]` = output wrapped in `<UNTRUSTED_DATASET_CONTENT>` (echoes raw query + GT text — the STATUS prefix on `diagnostics` is plain, only the dataset-content body is fenced). 🧩 follows every sub-member name — companion to the inline expansion the diagram does for `l1_overrides` (`n_variants`🧩, `creativity`🧩); lets you scan for atomic field names regardless of which placeholder owns them.

Each entry in `INJECTIONS` is a frozen `_Injection(name, kind, render, doc)`. `kind` is one of:

- **MEASUREMENT** — deterministic round-end output (e.g. `diagnostics`, `l1_wounds`, `guard_breaches`).
- **DERIVED** — view/digest over MeasurementArchive or AxisIndex (e.g. `axis_memory`).
- **TRACE** — narrative state from prior LLM calls (e.g. `critique`, `plan`, `task_context`).
- **DIRECTIVE** — short-lived directive consumed by exactly one downstream layer (e.g. `l3_to_l2_note`).

### Round-end measurement — what L1_SCORE + L1_CRITIQUE leave behind

This is where the reader's mental model of a round usually starts: candidates were scored, results condensed, critique produced. These outputs are what the next round's prompts read.

- ⁴ **`diagnostics`** ← STATUS prefix (plain) + fenced `RoundDiagnostics` body
  - **STATUS prefix** ← `bundle.cycle_slice` — `round`🧩, `current`🧩 (parent fitness `f"{cs.current_accuracy:.1%}"`), `best`🧩 (acc + round), `L1 stall`🧩 (rounds), and — when fired — `L2 fired`🧩 (count + stall), `L3 fired`🧩 (count + stall). Plain text (cycle counters are trusted optimizer state, not untrusted dataset content). Always renders, including pre-round-1 when `digest.diagnostics is None`. Despite the `current`/`best` accuracy labels, the rendered values are mean composite *fitness* under the active scorer, not hit-rates.
  - **Body** [fenced] ← `bundle.digest.diagnostics`: `RoundDiagnostics` from `compute_round_diagnostics` (built deterministically by L1_SCORE) — trajectory + recent-rounds evolution, anomalies, rank dist + top-k, pipeline-health termination split, failures by step / warning class, near-misses, cross-candidate diff, diversity, cache-share, miss-sample, prompt-size warning, probe outcome 🧩
- ⁵ **`l1_wounds`** [fenced] ← `opt_sp.wounds.{validation_failures, runtime_failures}` · L1-owned wounds, one block
  - **Validation** (parse-time): `axis`🧩, `value`🧩 (LLM-proposed), `allowed`🧩, `reason`🧩 — from `L1_SCHEMA_COMPLIANCE`; synthetic-0 per-candidate, except `reason=hallucinated_node` which is non-fatal routed signal.
  - **Runtime** (mid-eval): per-candidate `DegradationCheck` evidence, owner-tagged (`owner=l1` retune · `owner=operator` flagged, not in-loop fixable); accumulates cross-round (NEW vs ACCUMULATED).
  - Fenced (echoes arbitrary LLM output + pipeline warnings). Renderer `_r_l1_wounds`.
- ¹² **`guard_breaches`** ← `opt_sp.wounds.{l2_guard_breaches, l3_guard_breaches}` · post-parse breaches, both owner=L3 (replan)
  - Plain (only `validator_id`🧩 from a controlled registry — no untrusted content). Renderer `_r_guard_breaches`.
  - Set by L2/L3 post-parse validators. A non-empty L2 block force-triggers an immediate L3 fire (read off the stream by `escalate_l2`, not this render); L3 also reads its own past breaches to avoid repeating them.
- ⁸ **`critique`** ← `bundle.digest.critique` (L1_CRITIQUE output, consumes ⁴ + ⁵)
  - Compact view via `format_l1_critique_for_prompt`
  - `summary`🧩, `priority_fix`🧩, `suggested_axes`🧩, `failure_highlights`🧩 (top 5)

### Strategic injects — L3_PLAN writes, L2_CONTEXT refines, persistent

- **`plan`** ← `opt_sp.plan` · L3_PLAN-only writer; never cleared.
- ³ **`task_context`** ← `opt_sp.task_context`
  - 5 rendered fields: `domain`🧩, `pipeline_purpose`🧩, `data_characteristics`🧩, `optimization_goals`🧩, `key_challenges`🧩
  - Seeded by `checkin` decomposition at `init`; L2_CONTEXT merges on each fire — broadcast to every layer as the persistent task framing
  - 3 model-only sub-fields skipped by the renderer: `raw_description`🧩, `upstream_context`🧩, `downstream_context`🧩
- **`l3_to_l2_note`** ← `opt_sp.wounds.l3_note` · L2_CONTEXT template only; explicitly excluded from L1_GENERATE.

### Cross-round derived

- ¹⁴ **`axis_memory`** (DERIVED) ← `cycle.axes.digest()` — AxisIndex per-axis effect_size + sample-coverage; consumed by L1_GENERATE, L2_CONTEXT, L3_PLAN. Empty when AxisIndex isn't yet initialised (round 1).

### Current state

- **`rendered_prompt`** ← `opt_sp.render()`
  - 8-field `PromptTemplate` compiled to one string
  - Structurally L1_SCORE's output: each round's winner becomes next round's `opt_sp`, so its render is the next parent prompt. The cycle lives in orchestration, not the diagram.
- ⁹ **`pipeline_param_catalogue`** ← attributes on `pipeline_schema`: `node_param_keys`🧩, `param_allowed_values`🧩, `param_descriptions`🧩, `available_models`🧩
  - ≤4 enum values per param, ≤40-char description fallback, ≤8 models
- **`l1_overrides`** ← `opt_sp.l1_overrides`
  - Bundles two L2_CONTEXT-set knobs that govern *how L1_GENERATE runs*, not what L1_GENERATE puts in candidates: `n_variants`🧩, `creativity`🧩
  - `n_variants`🧩 enters L1_GENERATE only via the `{{n_variants}}` caller extra (a directive — L1_GENERATE obeys)
  - `creativity`🧩 sets the L1_GENERATE LLM call's temperature; never reaches the prompt text
  - Field, injection, and placeholder all share the name `l1_overrides`
- ⁷ **`l1_layout`** ← `opt_sp.l1_layout` · structural, not an INJECTION
  - L2_CONTEXT-only writer; consumed by `DispatchHub.fill` as `l1_generate`'s per-slot injection-name list that drives the slot walk (every node's layout comes from `NODE_LAYOUTS[node]`; `l1_generate`'s is L2-overridden via this field)
  - Decides *which* injection renderings land in each L1 addressable slot (`persona`🧩, `task_intent`🧩, `problem_description`🧩, `thinking_style`🧩) — content is rendered separately by the listed injections' `_r_*` functions
  - Not registered in `INJECTIONS`; never resolves a `{{l1_layout}}` placeholder. Shape-shifts L1's prompt rather than filling a slot in it

### L2_CONTEXT / L3_PLAN-internal

- ¹ **`l1_signal_catalogue`** ← sorted `L1_POSSIBLE` (`domain/l1_layout.py`) · menu L2_CONTEXT picks from when assembling L1_GENERATE's layout.
- **`prompt_budget_status`** (DERIVED) ← computed off `bundle.opt_sp` + the registry · L2_CONTEXT template only. The prompt-budget unit's L2 self-heal surface: every per-injection `char_cap` + the live size of any overrun, split into **YOURS** (`task_context`🧩, `l1_supplemental_rules`🧩, `l1_situational_examples`🧩 — L2 trims these) and **OTHER LAYERS** (flagged, not L2's to edit). `MANDATORY`-tier so the allocator never sheds the block that tells L2 how to heal. Full spec: `git log`.

### Caller extras — L1_GENERATE template scalars (`l1/generate.py`)

Substituted directly by `compile_prompt`; not signals.

- **`n_variants`** ← `min(opt_sp.l1_overrides["n_variants"], opt.n_variants × 3)` · directive — L1_GENERATE obeys.

## Mechanics

- **Fill** — one path for every node: `fill(template, layout, bundle)` walks the node's layout (per-slot injection-name lists — `l1_generate`'s from `opt_sp.l1_layout`, the rest from `NODE_LAYOUTS[node].floor`), appends rendered injection text to each addressable slot, then scans the filled body for any `{{name}}` left in non-layout prose (`instruction`/`answer_format`, e.g. `rebase_capability`) and renders the `INJECTIONS` ones into a kwargs dict → `(filled_template, injection_vars)`. The four meta-prompt `problem_description` bodies are now empty strings (their `{{tokens}}` moved into `NODE_LAYOUTS`). `validate_template()` (called from `load_optimizer_prompt`) errors at module load if any remaining `{{slot}}` is not in the `INJECTIONS` registry.
- **L1_GENERATE visibility** — `L1_POSSIBLE = {plan, task_context, rendered_prompt, pipeline_param_catalogue, diagnostics, l1_wounds, critique, axis_memory}` 🧩; the other injections (`l3_to_l2_note`, `l1_overrides`, `l1_signal_catalogue`, `prompt_budget_status`, `guard_breaches`) are L1_CRITIQUE / L2_CONTEXT / L3_PLAN-internal.
- **L1_GENERATE guard** — `L1_MANDATORY = {plan, task_context, rendered_prompt, pipeline_param_catalogue, critique}` 🧩 must appear across the 4 addressable slots; missing fires `l1_layout_missing_mandatory` — a guard breach that routes to L3_PLAN (replan) rather than letting L2_CONTEXT starve L1_GENERATE of cross-layer state.

## Future — diagnostics vs l1_wounds

The wound-render merge is **done**: validation + runtime render as one `l1_wounds` block, and the two guard streams as `guard_breaches`. What stays distinct is `diagnostics` vs `l1_wounds` — `diagnostics` is per-round on `Bundle.digest` while the wound streams accumulate on `OptSearchPoint` cross-round, so a shared `MeasurementReadout` would either duplicate state into `RoundDigest` or move accumulating fields off OSP (breaking per-candidate attribution). Storage stays the three typed `WoundChannels` lists **deliberately** (`self-healing-internals.md`) — only rendering collapsed. Park the diagnostics↔wounds merge until the readout shapes stabilise.
