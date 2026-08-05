# Dispatch hub + L1 layout

Visual + reference for `promptpotter/application/optimization/dispatch/` — the registry that fills `{{placeholders}}` in the four optimizer prompts — and for `L1Layout`, the structural surface L2 edits to decide what L1_GENERATE sees. Pairs with [`l2-internals.md`](l2-internals.md) (L2_CONTEXT firing).

The hub is stateless. `INJECTIONS` is a typed `dict[str, _Injection]` — each entry carries `name`, `kind` (MEASUREMENT / DERIVED / TRACE / DIRECTIVE), `render: InjectionBundle → str`, a `char_cap`, a `citable` flag, and a `description` string, registered by the `@signal("<name>", …)` decorator at the renderer's definition site. `validate_template()` (called from `load_optimizer_prompt`) raises on `{{slot}}` names not in the registry: a typo in a template fails at module load, not at first render.

`citable` answers one question: may an `l1_generate` variant name this panel in `evidence_grounding`? True for panels that REPORT (what was measured, what failed, what the layers steered); False for the value-space menus and the prompt under edit — citing those grounds a mutation in its own subject. `citable_fields(layout, exploration_budget)` intersects the flag with the node's **live layout**, so what L1 may cite is exactly what L1 was shown; the same call fills the prompt's `{{citable_fields}}` menu, the wire schema's enum, and the `evidence_grounding_present` check. Adding a panel to a floor makes it citable automatically.

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
    BLK[prompt_block_catalogue¹³]:::det
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

  %% L1_CRITIQUE inputs (the distiller reads raw round output, not the strategic frame —
  %% plan/task_context are not in its layout vocabulary)
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

**25 registered signals** across four modules (`injections/layer_state.py` · `panels.py` · `catalogues.py` · `wounds.py` — each renderer is its slot's SoT), plus 1 structural input (`l1_layout`) and 2 caller extras (`n_variants`, `citable_fields`). The highest-traffic slots are detailed below, grouped by role; numbered items map to the diagram superscripts. `[fenced]` = output wrapped in `<UNTRUSTED_DATASET_CONTENT>` (echoes raw query + GT text — the STATUS prefix on `diagnostics` is plain, only the dataset-content body is fenced). 🧩 follows every sub-member name — companion to the inline expansion the diagram does for `l1_overrides` (`n_variants`🧩, `creativity`🧩); lets you scan for atomic field names regardless of which placeholder owns them.

Each entry in `INJECTIONS` is a frozen `_Injection(name, kind, render, char_cap, citable)`. `kind` is one of:

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
- **Panels family** (`injections/panels.py`, DERIVED views; each renderer is its slot's SoT): `escalation_panel` (L1 stall depth + `exploration_budget` — gates the `stall_exploration` citation), `evidence_health` (per-node failure rates — flags an evidence-starved enricher), `answer_distribution` (what the pipeline ANSWERS vs what is true, as label tallies, plus the score a constant single-label answer would earn — the collapse detector; empty on free-text answer spaces. It renders the rule but no longer owns it: `domain/scoring.py::enumerable_truth_labels` is the one definition of "is there a constant to detect here", shared with the scoring gate that withholds θ from a collapsed candidate and with PoBB, which eliminates it), `failing_samples` (every current miss, one line each, ordered easiest-first on the cycle's locked δ ruler — which samples are still failing, how hard each is, what was answered vs what was true), `mutation_memory` (what this cycle has ALREADY tried: the changed field and its value, what it scored against its own matched origin, how it ended — keyed on the payload, never on the LLM's `changes_description`), `origin_strengths` (what the round-0 origin already scores, the floor variants must preserve), `archive_top_runs` (top-K historical runs on this dataset), `rare_hit_samples` (samples cracked by ≤3 of ≥10 attempts).
- **Capability directives** (`injections/layer_state.py`): `rebase_capability` / `terminate_capability` (conditional escape-hatch instructions into L2+L3 prompts; render empty when the config knob is off so ablation prompt bodies stay bit-identical). They **ride the layout channel** — on the `l2_context`/`l3_plan` floors AND mandatory sets, so an L4 layout edit that excises one rolls back to the floor (`validate_l1_layout`); no prose `{{token}}` carries them, so a prose rewrite *cannot* drop them. The config bit is the one sanctioned way to silence a directive. The base optimizer prompts' remaining prose tokens are the INLINE caller extras (`{{n_variants}}`, `{{citable_fields}}` on `l1_generate`) — ports that can never ride layout (they sit mid-sentence), guarded instead by `L1_PROMPT_PLACEHOLDERS_INTACT` → `dropped_mandatory_placeholder` (synthetic-0), checked on the MERGED params so inherited breakage flags too.

### Current state

- **`rendered_prompt`** ← `opt_sp.render()` **⊕ `effective_optimizer_prompts(pipeline_schema, pipeline_params)`**
  - 8-field `PromptTemplate` compiled to one string
  - Structurally L1_SCORE's output: each round's winner becomes next round's `opt_sp`, so its render is the next parent prompt. The cycle lives in orchestration, not the diagram.
  - The panel is **the artifact under edit**, and on the recursion that is not the searchpoint: an L4 outer point's prompt fields reach no node (`prompt_node_names()` is empty there), while the real levers are the inner nodes' own `PromptTemplate` fields carried as `pipeline_params`. Both halves render, each empty where it is not the mutation surface — so a normal campaign is bit-identical and L4 stops rendering a MANDATORY panel as nothing. Base ⊕ the incumbent's adopted overrides, whole fields only: every mutation here is a complete-field replacement, so a truncated render would be worse than an absent one (hence the cap sits above the recursion's own ~10k bundle).
- ⁹ **`pipeline_param_catalogue`** ← attributes on `pipeline_schema`: `node_param_keys`🧩, `param_allowed_values`🧩, `param_descriptions`🧩, `available_models`🧩
- ¹³ **`prompt_block_catalogue`** ← `config/prompt_variants.json` (`prompt_blocks()`), gated by `OptimizationConfig.prompt_block_catalogue`. The value space of a prompt FIELD, as `pipeline_param_catalogue` is the value space of a pipeline PARAM. `guidance` (default) offers the blocks as reusable material L1 may adapt or ignore; `restrict` closes the field to the library (an off-library value fails `L1_PROMPT_BLOCKS_IN_LIBRARY` → synthetic-0 → L2 wound, the same shape as a forbidden axis); `off` renders empty, leaving the prompt bit-for-bit identical to a no-library ablation.
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

### Caller extras — L1_GENERATE template scalars (`l1/generate.py`)

Substituted directly by `compile_prompt`; not signals.

- **`n_variants`** ← `min(opt_sp.l1_overrides["n_variants"], opt.n_variants × 3)` · directive — L1_GENERATE obeys.

## Mechanics

- **Entry points** — two, both stateless: `render(name, bundle)` (internal, one injection's text) and `fill(template, layout, bundle)` (**every** optimizer node). `InjectionBundle` is the per-call frozen state `(opt_sp, pipeline_schema, cycle_slice, digest)`, built once via `build_bundle(cycle)`; `digest` is a `RoundDigest(diagnostics, critique)` — the post-scoring compression chain in one place, so renderers read through it instead of off two parallel `latest_*` fields.
- **Fill** — one path for every node: `fill(template, layout, bundle)` walks the node's layout (per-slot injection-name lists — `l1_generate`'s from `opt_sp.l1_layout`, the rest from `NODE_LAYOUTS[node].floor`), appends rendered injection text to each addressable slot, then scans the filled body for any `{{name}}` left in non-layout prose (`instruction`/`answer_format`) and renders the `INJECTIONS` ones into a kwargs dict → `(filled_template, injection_vars)`. The four optimizer prompt `problem_description` bodies are now empty strings (their `{{tokens}}` moved into `NODE_LAYOUTS`), and **no optimizer prose token names an injection any more** — every surviving `{{token}}` in the shipped prompts is a caller extra (`n_variants`, `citable_fields`, `consultation_instruction`). The `l2_context` instruction kept `{{rebase_capability}}`/`{{terminate_capability}}` after that move, so both directives rendered TWICE in every L2 prompt (~1.5k of ~10.9k chars, measured on the banked ledgers) — deleted; the layout channel is the only one. `validate_template()` (called from `load_optimizer_prompt`) errors at module load if any remaining `{{slot}}` is not in the `INJECTIONS` registry.
- **L1_GENERATE visibility** — `L1_POSSIBLE` (`domain/l1_layout.py`, 18 names) = `{plan, task_context, rendered_prompt, pipeline_param_catalogue, prompt_block_catalogue, diagnostics, l1_wounds, critique, answer_distribution, failing_samples, inner_narratives, mutation_memory, axis_memory, escalation_panel, origin_strengths, archive_top_runs, rare_hit_samples, sample_transcripts}` 🧩; the rest (`l3_to_l2_note`, `l1_overrides`, `l1_signal_catalogue`, `guard_breaches`, the capability directives) are L1_CRITIQUE / L2_CONTEXT / L3_PLAN-internal — L2-internal signals are excluded so L1 can't see L2's own state.
- **L1_GENERATE guard** — `L1_MANDATORY = {plan, task_context, rendered_prompt, pipeline_param_catalogue, critique}` 🧩 must appear across the 4 addressable slots; missing fires `l1_layout_missing_mandatory` — a guard breach that routes to L3_PLAN (replan) rather than letting L2_CONTEXT starve L1_GENERATE of cross-layer state (without these L1 has no parent prompt, plan, task framing, mutation surface, or failure digest).

## L1 layout — L2's structural edit surface

L1_GENERATE's prompt is composed by walking a per-slot list of **injection names** and resolving each through the registry above. L2 owns the layout; the registry is closed and code-derived. Concept role: [`the-loop.md`](../concepts/the-loop.md).

```
┌─ L1's prompt composition ──────────────────────────────────┐
│  PromptTemplate (l1_generate)        per-slot static text  │
│      +                                                     │
│  L1Layout (on OptSearchPoint)    per-slot injection lists  │
│      ↓                                                     │
│  DispatchHub.fill                    resolves names via    │
│                                      INJECTIONS registry   │
│      ↓                                                     │
│  RENDERED L1 PROMPT (what the LLM sees)                    │
└────────────────────────────────────────────────────────────┘
```

`L1Layout` (`promptpotter/domain/l1_layout.py`) is a Pydantic model with one list per addressable slot: `persona`, `task_intent`, `problem_description`, `thinking_style` (all L2-mutable). `answer_format` is omitted on purpose — it carries L1's output JSON schema (a code contract), not L2's call. Static text in each slot stays; the layout's injection renderings are appended. Renderers are layer-agnostic — the same `plan` renderer feeds L1, L2, and L3; if an injection needs to differ per layer, that's two injections.

**Default floor** (`default_l1_layout` = `NODE_LAYOUTS["l1_generate"].floor`): `task_context` in `task_intent`; `rendered_prompt`, `pipeline_param_catalogue`, `prompt_block_catalogue`, `plan`, `answer_distribution`, `critique`, `failing_samples`, `inner_narratives`, `mutation_memory`, `l1_wounds`, `escalation_panel`, `origin_strengths` in `problem_description`. `answer_distribution` leads the evidence because it frames everything after it: a pipeline collapsed onto one label needs that break, not a better-argued instruction, and no other panel can say so. `sample_transcripts` stays OFF the floor — it is the same misses at ~5x the bytes, and the generator reading it beside the critique duplicated a ~10k payload every round; `failing_samples` is its dense peer. Raw `diagnostics` and the cross-run panels stay off the floor too (critique distils them); L2 adds them on stall via its layout edit, and L4 optimises that authoring. Most L2 fires don't touch the layout.

**Validation — split HARD / SOFT.** `validate_l1_layout(layout, *, spec, prior_layout)` enforces against the node's `NodeLayoutSpec` (`spec.mandatory`/`spec.possible`):

- HARD — missing mandatory placeholder, name outside the node's `possible`, duplicate within a slot. Caller rolls back to the prior layout / floor; outcomes append to the guard-breach wound stream for self-healing on the next L2 fire.
- SOFT — layout unchanged from prior. Applied with a warning; flagged `score=0.5` so L3 sees the churn signal next replan.

L2's parser (`escalation._parse_l2`) coerces `{slot: [name, ...]}` into `L1Layout`, validates, and only writes the new layout to OSP when HARD checks pass.

**Adding an injection** → the golden-path recipe lives in [`adding-a-surface.md`](adding-a-surface.md).

**File-line anchors** — `INJECTIONS`: `dispatch/injections/registry.py` · `InjectionBundle`: `dispatch/bundle.py` · `DispatchHub` + `build_bundle`: `dispatch/facade.py` · `L1Layout`, `L1_POSSIBLE`, `L1_MANDATORY`, `L1_LAYOUT_SLOTS`, `default_l1_layout`, `validate_l1_layout`: `promptpotter/domain/l1_layout.py` · L1 compose path: `application/optimization/l1/generate.py::l1_generate` · OSP layout field: `OptSearchPoint.memory.l1_layout` (`domain/opt_search_point.py`, `L2L3Memory`).

## Future — diagnostics vs l1_wounds

The wound-render merge is **done**: validation + runtime render as one `l1_wounds` block, and the two guard streams as `guard_breaches`. What stays distinct is `diagnostics` vs `l1_wounds` — `diagnostics` is per-round on `Bundle.digest` while the wound streams accumulate on `OptSearchPoint` cross-round, so a shared `MeasurementReadout` would either duplicate state into `RoundDigest` or move accumulating fields off OSP (breaking per-candidate attribution). Storage stays the three typed `WoundChannels` lists **deliberately** (`self-healing-internals.md`) — only rendering collapsed. Park the diagnostics↔wounds merge until the readout shapes stabilise.
