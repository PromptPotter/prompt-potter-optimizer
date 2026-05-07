# Dispatch hub

Visual + reference for `promptpotter/application/optimization/dispatch_hub.py` — the registry that fills `{{placeholders}}` in the four optimizer prompts. Pairs with [`l1-generate-surface.md`](l1-generate-surface.md) (L1_GENERATE's layout surface) and [`l2-internals.md`](l2-internals.md) (L2_CONTEXT firing).

The hub is stateless. Each `_r_*` renderer in `SIGNALS` is the construction recipe for one placeholder; the hub looks the name up, renders, returns a dict.

## Flow

Inputs (left) fill placeholders in optimizer process nodes (right).

- **Amber pill** — optimizer process node: the four LLM prompts (L1_GENERATE, L1_CRITIQUE, L2_CONTEXT, L3_PLAN) plus L1_SCORE (deterministic).
- **Solid orange** — deterministic input (schema, measurements, counters, constants).
- **Default node** — AI-generated input (content originates from another LLM stage).
- **Red arrow** — LLM-produced edge: the feedback loops that close the optimizer. L1_SCORE outputs (`diagnostics`, `failures`, `accuracy_pct`) are computed, so they draw in normal color.

```mermaid
flowchart LR
  classDef det fill:#FFA500,stroke:#cc7a00,color:#000
  classDef proc fill:#fff3d6,stroke:#f59e0b,stroke-width:4px,color:#000

  %% Standalone AI-generated inputs (not produced inside a single LLM stage)
  L1PF[l1gen_prompt_fields⁷]
  CPAR["l1_config<br/>• n_variants<br/>• temp"]

  %% Standalone deterministic inputs
  CPOS[cycle_position⁶]:::det
  DIAG[diagnostics⁴]:::det
  FAIL[failures⁵]:::det
  AP[accuracy_pct²]:::det

  %% Loop-Settings — read-only loop-time constants
  subgraph LS["Loop-Settings"]
    TUN[tunable_params⁹]:::det
    NQ[n_queries]:::det
    CAT[l1_signal_catalogue¹]:::det
  end
  style LS fill:none,stroke:#888,stroke-dasharray: 5 5

  %% L1_GENERATE + L1_SCORE + L2_CONTEXT — standalone process nodes
  L1G([L1_GENERATE]):::proc
  L1S([L1_SCORE]):::proc
  L2P([L2_CONTEXT]):::proc

  %% Standalone L2-produced signals
  TC[task_context³]
  L2H[l2_history¹⁰]

  %% L1_CRITIQUE + its produced signal
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

  %% L1_GENERATE inputs (default layout + caller extras + L1PF self-view)
  PLAN --> L1G
  TC --> L1G
  TUN --> L1G
  DIAG --> L1G
  FAIL --> L1G
  CRIT --> L1G
  CPAR --> L1G
  AP --> L1G
  NQ --> L1G
  L1PF --> L1G

  %% L1_CRITIQUE inputs
  PLAN --> L1C
  TC --> L1C
  DIAG --> L1C
  FAIL --> L1C

  %% L2_CONTEXT inputs
  PLAN --> L2P
  L3N --> L2P
  CPOS --> L2P
  DIAG --> L2P
  FAIL --> L2P
  CRIT --> L2P
  CPAR --> L2P
  TC --> L2P
  CAT --> L2P
  L1PF --> L2P

  %% L3_PLAN inputs
  PLAN --> L3P
  TC --> L3P
  L2H --> L3P
  CPOS --> L3P
  DIAG --> L3P
  FAIL --> L3P
  CRIT --> L3P
  L1PF --> L3P

  %% LLM-produced feedback edges (red)
  L3P --> PLAN
  L3P --> L3N
  L2P --> TC
  L2P --> CPAR
  L2P --> L2H
  L2P --> L1PF
  L1C --> CRIT

  %% L1_SCORE derivations — deterministic; normal color
  L1S --> DIAG
  L1S --> FAIL
  L1S --> AP

  %% Red styling for the 7 LLM-produced feedback edges (edges 32..38)
  linkStyle 32,33,34,35,36,37,38 stroke:#B22222,stroke-width:2px
```

## Reference

13 signals + 3 caller extras, grouped by role. Numbered items map to the diagram superscripts. `[fenced]` = output wrapped in `<UNTRUSTED_DATASET_CONTENT>` (echoes raw query + GT text). 🧩 follows every sub-member name — companion to the inline expansion the diagram does for `l1_config` (`n_variants`🧩, `creativity`🧩); lets you scan for atomic field names regardless of which placeholder owns them.

### Round-end measurement — what L1_SCORE + L1_CRITIQUE leave behind

This is where the reader's mental model of a round usually starts: candidates were scored, results condensed, critique produced. These outputs are what the next round's prompts read.

- ⁴ **`diagnostics`** [fenced] ← `bundle.digest.diagnostics`
  - `RoundDiagnostics` from `compute_round_diagnostics` (built deterministically by L1_SCORE)
  - trajectory + recent-rounds evolution, anomalies, rank dist + top-k, pipeline-health termination split, failures by step / warning class, near-misses, cross-candidate diff, diversity, cache-share, miss-sample, prompt-size warning, probe outcome 🧩
- ⁵ **`failures`** [fenced] ← attributes on `opt_sp`: `validation_failures`🧩, `runtime_failures`🧩, `escalation_log`🧩, `warning_inventory`🧩, `l2_output_failures`🧩, `l3_output_failures`🧩
  - Accumulates across rounds — that's why it lives on `OptSearchPoint`, not in the per-round `Bundle.digest`
- ⁸ **`critique`** ← `bundle.digest.critique` (L1_CRITIQUE output, consumes ⁴ + ⁵)
  - Compact view via `format_l1_critique_for_prompt`
  - `summary`🧩, `priority_fix`🧩, `suggested_axes`🧩, `failure_highlights`🧩 (top 5)

`accuracy_pct` is also an L1_SCORE-derived readout but reaches L1_GENERATE as a template scalar, not a signal — see *Caller extras* below.

### Strategic injects — L3_PLAN writes, L2_CONTEXT refines, persistent

- **`plan`** ← `opt_sp.plan` · L3_PLAN-only writer; never cleared.
- ³ **`task_context`** ← `opt_sp.task_context`
  - 5 rendered fields: `domain`🧩, `pipeline_purpose`🧩, `data_characteristics`🧩, `optimization_goals`🧩, `key_challenges`🧩
  - Seeded by `restructure` decomposition at `init`; L2_CONTEXT merges on each fire — broadcast to every layer as the persistent task framing
  - 3 model-only sub-fields skipped by the renderer: `raw_description`🧩, `upstream_context`🧩, `downstream_context`🧩
- **`l3_to_l2_note`** ← `opt_sp.l3_note` · L2_CONTEXT template only; explicitly excluded from L1_GENERATE.

### Current state

- **`rendered_prompt`** ← `opt_sp.render()`
  - 8-field `PromptTemplate` compiled to one string
  - Structurally L1_SCORE's output: each round's winner becomes next round's `opt_sp`, so its render is the next parent prompt. The cycle lives in orchestration, not the diagram.
- ⁹ **`tunable_params`** ← attributes on `pipeline_schema`: `node_param_keys`🧩, `param_allowed_values`🧩, `param_descriptions`🧩, `available_models`🧩
  - ≤4 enum values per param, ≤40-char description fallback, ≤8 models
- **`l1_config`** ← `opt_sp.l1_config`
  - Bundles two L2_CONTEXT-set knobs that govern *how L1_GENERATE runs*, not what L1_GENERATE puts in candidates: `n_variants`🧩, `creativity`🧩
  - `n_variants`🧩 enters L1_GENERATE only via the `{{n_variants}}` caller extra (a directive — L1_GENERATE obeys)
  - `creativity`🧩 sets the L1_GENERATE LLM call's temperature; never reaches the prompt text
  - Field, signal, and placeholder all share the name `l1_config`

### L2_CONTEXT / L3_PLAN-internal

- ¹ **`l1_signal_catalogue`** ← sorted `L1_POSSIBLE` (`domain/l1_layout.py`) · menu L2_CONTEXT picks from when assembling L1_GENERATE's layout.
- ⁷ **`l1gen_prompt_fields`** ← recursive `fill_l1` over current `opt_sp.l1_layout` against L1_GENERATE's 8-field template
  - Snapshot of what L1_GENERATE will receive next round
  - L2_CONTEXT is the sole writer (via the layout) — hence no `L1G → l1gen_prompt_fields` arrow in the diagram
  - 4 addressable slots: `persona`🧩, `task_intent`🧩, `problem_description`🧩, `thinking_style`🧩
  - 2 template-fixed (non-addressable) slots: `instruction`🧩, `answer_format`🧩
- ⁶ **`cycle_position`** ← `bundle.cycle_slice`
  - Round, best (acc + round), current acc, L1_GENERATE / L2_CONTEXT / L3_PLAN stalls, probe-scheduled flag 🧩
- ¹⁰ **`l2_history`** ← `cycle_slice.l2_round`🧩 + prior `opt_sp.l1_config` snapshot + Δ best-fitness since L3_PLAN entry · L3_PLAN-only synthetic recap.

### Caller extras — L1_GENERATE template scalars (`l1.py:120-124`)

Substituted directly by `compile_prompt`; not signals.

- **`n_variants`** ← `min(opt_sp.l1_config["n_variants"], opt.n_variants × 3)` · directive — L1_GENERATE obeys.
- ² **`accuracy_pct`** ← `f"{cycle.tracking.current_accuracy:.1%}"`
  - Despite the name, this is the parent searchpoint's mean composite *fitness* under the active scorer, not a hit-rate
  - `current_accuracy` is the historical tracker attribute on the cycle
- **`n_queries`** ← `len(cycle.tracking.current_results)` · scoring-set size.

## Mechanics

- **Fill** — L1_GENERATE slot bodies are plain text; `fill_l1` walks `opt_sp.l1_layout` (per-slot signal-name lists) and appends rendered signal text to each slot. L1_CRITIQUE / L2_CONTEXT / L3_PLAN bodies carry literal `{{name}}` markers; `fill_fixed` regex-extracts and resolves them.
- **L1_GENERATE visibility** — `L1_POSSIBLE = {plan, task_context, rendered_prompt, tunable_params, diagnostics, failures, critique}` 🧩; the other 6 signals are L2_CONTEXT / L3_PLAN-internal.
- **L1_GENERATE guard** — `L1_MANDATORY = {plan, task_context, rendered_prompt, tunable_params, critique}` 🧩 must appear across the 4 addressable slots; missing fires `l1_layout_missing_mandatory` with `nurse_target='l3'` — L3_PLAN replans rather than letting L2_CONTEXT starve L1_GENERATE of cross-layer state.
