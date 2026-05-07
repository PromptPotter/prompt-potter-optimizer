# Dispatch hub — placeholder index + flow

Visual reference for `promptpotter/application/optimization/dispatch_hub.py`. Pairs with [`l1-generate-surface.md`](l1-generate-surface.md) (L1 layout mechanics) and [`l2-internals.md`](l2-internals.md) (L2 firing + output).

The hub is stateless. Every `_r_*` renderer in `SIGNALS` is the construction recipe for one placeholder; the hub just looks the name up.

## Flow

Inputs on the left fill `{{placeholders}}` in the optimizer process nodes on the right.

- **Amber-rimmed pill node** = optimizer process node — the four LLM prompts (L1_GENERATE, L1_CRITIQUE, L2_CONTEXT, L3_PLAN) plus L1_SCORE (the deterministic scoring/winner-selection node). Same accent color as the webapp's `--color-accent`.
- **Solid orange node** = deterministic input (schema, round measurements, cycle counters, code constants — no LLM in the chain).
- **Default node** = AI-generated input.
- **Red arrow** (firebrick) = an LLM process node that *produces* this input — the LLM-driven feedback edges that close the optimizer's loops. L1_SCORE is deterministic, so its outputs to `diagnostics` / `failures` (computed from measurements) and `accuracy_pct` are drawn in normal color. The L1_GENERATE ↔ L1_SCORE candidate-flow and winner-selection happen between rounds and aren't drawn — the diagram is about placeholder fill, not orchestration.

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

> ¹ **`l1_signal_catalogue`** — sorted `L1_POSSIBLE` constant; the menu of names L2 may put in L1's layout. See the [Placeholder index](#placeholder-index) below for the full signal set.
>
> ² **`accuracy_pct`** — the parent searchpoint's mean composite **fitness** on the active scoring set, formatted as a percentage (e.g. `93.0%`). Despite the name, this is the continuous formula-driven score (whatever `compute_composite_score()` resolves to under the active `ScoringEnv.scorer`), not a hit-rate. Injected as `f"{cycle.tracking.current_accuracy:.1%}"` — `current_accuracy` is the historical attribute name kept on the cycle-tracker; the value is fitness.
>
> ³ **`task_context`** — five framing fields refined by L2 (broadcast to every layer): `domain`, `pipeline_purpose`, `data_characteristics`, `optimization_goals`, `key_challenges`. Three additional sub-fields (`raw_description`, `upstream_context`, `downstream_context`) live on the model but are skipped by the renderer.
>
> ⁴ **`diagnostics`** — `RoundDiagnostics` from `compute_round_diagnostics`: trajectory + recent-rounds evolution, anomalies, rank distribution + top-k accuracy, pipeline-health termination split, failures-by-step, failures-by-warning classes, near-misses, cross-candidate diff, population diversity / cache-share, miss sample diagnostics, prompt-size warning, probe outcome. Wrapped in `<UNTRUSTED_DATASET_CONTENT>` (echoes raw query/GT text).
>
> ⁵ **`failures`** — six sub-streams off `OptSearchPoint`: `validation_failures`, `runtime_failures`, `escalation_log`, `warning_inventory`, `l2_output_failures`, `l3_output_failures`. Wrapped in `<UNTRUSTED_DATASET_CONTENT>`.
>
> ⁶ **`cycle_position`** — round counter, best (acc + round), current acc, L1 stall, L2 fire / stall, L3 fire / stall, probe-scheduled flag.
>
> ⁷ **`l1gen_prompt_fields`** — L1_GENERATE's six `PROMPT_STRING_FIELDS` after layout fill: `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`. Layout addresses four (`persona`, `task_intent`, `problem_description`, `thinking_style`); `instruction` and `answer_format` are template-fixed.
>
> ⁸ **`critique`** — compact view of the most recent L1_CRITIQUE output dict (via `format_l1_critique_for_prompt`): `summary`, `priority_fix`, `suggested_axes`, `failure_highlights` (top 5).
>
> ⁹ **`tunable_params`** — pulled from `pipeline_schema`: `node_param_keys`, `param_allowed_values` (≤4 enum values shown per param), `param_descriptions` (≤40-char fallback), `available_models` (capped at 8).
>
> ¹⁰ **`l2_history`** — L3-only synthetic recap: `l2_round` counter, the prior `l1_config` snapshot, and `acc_change` (Δ best_composite_fitness vs. L3-entry baseline).

> Why "AI-generated" for `l1gen_prompt_fields` / `task_context` / `l1_config` / `l2_history`: their *content* originates from L2 (mutations of `opt_sp.l1_layout` reshape `l1gen_prompt_fields`; L2 merges into `task_context`; L2/L3 set `l1_config`). The format is deterministic; the strings injected are not. `task_context`'s initial value is seeded by a one-time `restructure` decomposition LLM at `init` time; L2 then refines it each fire — broadcast to every prompt as the persistent task framing.
>
> `l1_config` bundles two L2-set knobs that govern *how L1 runs*, not what L1 puts in candidates: `n_variants` enters L1's prompt as the `{{n_variants}}` caller extra (a directive — L1 obeys); `creativity` sets the L1 LLM call's temperature (configures the API call, never the prompt text). Field, signal, and placeholder all share the name `l1_config`.
>
> `l1gen_prompt_fields` is a snapshot of L1_GENERATE's PromptTemplate as L1 will receive it next round: the 8-field template with the current `opt_sp.l1_layout` (L2's surface) compiled through `fill_l1`. The arrow `L1_GENERATE → l1gen_prompt_fields` is intentionally absent — L1 reads these fields, it doesn't produce them. The only writer is L2, via the layout (`L2_CONTEXT → l1gen_prompt_fields`).
>
> The `rendered_prompt` SIGNAL still exists in code (it's in `SIGNALS` and L1's default layout) — it's literally `opt_sp.render()`, the 8-field `PromptTemplate` compiled into one string. Structurally it's L1_SCORE's output: each round's winner becomes the new `opt_sp`, and next round's L1_GENERATE reads `opt_sp.render()` as its parent prompt. The cycle isn't drawn explicitly — it lives in orchestration (between-round state update), not in placeholder dispatch.

## Placeholder index

13 signals + 3 caller extras. `[fenced]` signals wrap output in `<UNTRUSTED_DATASET_CONTENT>` for prompt-injection hardening (echo raw query text + GT + warnings).

```
DISPATCH v2 — 13 signals + 3 caller extras
│
├─ Strategic injects (written by another LLM layer)
│   ├─ {{plan}}            ← opt_sp.plan         (L3 writes; persistent, never cleared)
│   ├─ {{task_context}}    ← opt_sp.task_context (seeded by restructure decomposition at init;
│   │                                              L2 refines via merge — broadcast to every layer)
│   └─ {{l3_to_l2_note}}   ← opt_sp.l3_note      (L2 template only — L1 explicitly excluded)
│
├─ Current state
│   ├─ {{rendered_prompt}} ← opt_sp.render() of the 8-field PromptTemplate
│   ├─ {{tunable_params}}  ← pipeline_schema.{node_param_keys, param_allowed_values,
│   │                                          param_descriptions, available_models}
│   └─ {{l1_config}}       ← opt_sp.l1_config             (L2 reads as state;
│                                                          L1 reads contents via caller extras)
│
├─ Round-end measurement — bundle.digest + opt_sp failure streams
│   ├─ {{diagnostics}}     ← bundle.digest.diagnostics (RoundDiagnostics from
│   │                                                  compute_round_diagnostics)        [fenced]
│   ├─ {{critique}}        ← bundle.digest.critique (raw L1_CRITIQUE LLM output dict)
│   └─ {{failures}}        ← opt_sp.{validation_failures, runtime_failures, escalation_log,
│                                     warning_inventory, l2_output_failures,
│                                     l3_output_failures}                                [fenced]
│
│   `digest` is one round's compression chain (deterministic readout +
│   AI-generated critique) wrapped on `Bundle`. `failures` accumulates
│   across rounds, so it stays on `OptSearchPoint`.
│
├─ L2/L3-internal context
│   ├─ {{l1_signal_catalogue}} ← sorted L1_POSSIBLE constant — menu L2 picks from for layouts
│   ├─ {{l1gen_prompt_fields}} ← L1_GENERATE's 8-field template, layout-filled (recursive
│   │                                                  fill_l1 over current opt_sp.l1_layout)
│   ├─ {{cycle_position}}      ← bundle.cycle_slice (round, accuracies, stall counts, probe flag)
│   └─ {{l2_history}}          ← cycle_slice.l2_round + opt_sp.l1_config +
│                                  Δ since L3 entry      (L3 template only)
│
└─ L1_GENERATE template scalars (NOT signals — caller extras at l1.py:123-127)
    ├─ {{n_variants}}     ← min(opt_sp.l1_config["n_variants"], opt.n_variants × 3)
    ├─ {{accuracy_pct}}   ← f"{cycle.tracking.current_accuracy:.1%}"
    └─ {{n_queries}}      ← len(cycle.tracking.current_results)
```

## Per-layer placement

| Layer | Template body has `{{...}}`? | How signals reach the LLM |
|---|---|---|
| L1_GENERATE  | No — slot bodies are plain text | `fill_l1` walks `opt_sp.l1_layout` (per-slot lists of signal names) and appends rendered signal text to each slot's static body. The 3 caller extras are then substituted by `compile_prompt`. |
| L1_CRITIQUE  | Yes (`{{plan}}`, `{{task_context}}`, `{{diagnostics}}`, `{{failures}}`) | `fill_fixed` regex-extracts `{{name}}`, returns `{name: rendered}`. |
| L2           | Yes | Same as L1_CRITIQUE. |
| L3           | Yes — plus `{{l2_history}}` available. | Same. |

L1 sees only `L1_POSSIBLE = {plan, task_context, rendered_prompt, tunable_params, diagnostics, failures, critique}`. The other six signals are L2/L3-internal.

L1 layout HARD-validates that `L1_MANDATORY = {plan, task_context, rendered_prompt, tunable_params, critique}` appears somewhere across the four addressable slots (`persona`, `task_intent`, `problem_description`, `thinking_style` — `answer_format` is template-fixed). Dropping any mandatory placeholder fires `l1_layout_missing_mandatory` with `nurse_target='l3'` — L3 replans the strategy rather than letting L2 starve L1 of cross-layer state.
