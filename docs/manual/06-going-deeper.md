# Going deeper

You've run a campaign. Pointers below for the next layer.

## Concepts — how it works

| Page | Covers |
|------|--------|
| [Three-layer loop](../concepts/the-loop.md) | Why L1/L2/L3 exist and when each fires |
| [Self-healing](../concepts/self-healing.md) | Recovery from bad proposals + degraded runs |
| [Scoring and memory](../concepts/scoring-and-memory.md) | Traces are facts, scores are policy + the cross-campaign archive |
| [State record](../concepts/state-record.md) | The candidate's state record (`OptSearchPoint` in code) — carries task context, plan, prompt fields, L2 overrides |
| [Campaign tree](../concepts/campaign-tree.md) | Cycles, forks, and the sweep primitive |
| [Nodes and pipelines](../concepts/nodes-and-pipelines.md) | Pipeline node anatomy |
| [Glossary](../concepts/glossary.md) | Terms used across the docs |

## Operations

| Page | Covers |
|------|--------|
| [CLI reference](../operations/cli-reference.md) | Every subcommand, flag, and env variable |
| [Backend integration](../operations/backend-integration.md) | The contract a backend must implement |
| [Persistence and state](../operations/persistence-and-state.md) | The `.promptpotter/` tree, resume, rewind, fork, scoring steer |
| [Observability](../operations/observability.md) | Langfuse integration |

## Developer — implementation

| Page | Covers |
|------|--------|
| [Developer README](../developer/README.md) | Prompt structure, request routing, the scoring step, learning from prior campaigns + per-field reference tables |
| [L2 internals](../developer/l2-internals.md) | L2 firing, output, OSP mutations, layout edits |
| [L1 layout + dispatch hub](../developer/l1-generate-surface.md) | `SIGNALS` registry, `L1Layout`, `DispatchHub` |
| [Self-healing internals](../developer/self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](../developer/node-standard.md) | Wiring a new pipeline node |

## Methods — the statistics

- [Candidate elimination](../methods/candidate-elimination.md) — Bayesian Posterior-of-Being-Best (PoBB).
- [Exploration/exploitation sample selection](../methods/exploration-exploitation.md) — Rasch + Knowledge Gradient.

## Research — benchmarks and the paper

- [Benchmarks](../research/benchmarks.md) — datasets, methodology, baselines.
- [Metrics](../research/metrics.md) — beyond absolute accuracy (HC, SE, R₉₀).
- [Related work](../research/related-work.md) — competitor positioning.

---

## Iterating on prompts manually

When you want to tune `l1_generate` (or another optimizer prompt) by hand instead of running the full L1/L2 loop. Five-step single-cycle cadence:

```
1. python -m promptpotter optimize         # full mode
2. /potter-review                          # round-1 gate
3. Operator confirms or redirects the proposed fix.
4. Claude applies the edit (Edit tool, prompt file).
5. Operator re-runs optimize.
```

The skill is mandatory after round 1. Proceeding past round 1 with `round_1_verdict ≠ healthy` is an operator override.

### Round-1 verdict rule

Source: `application/optimization/l1_stats.py::compute_round_1_verdict`. `HEALTHY_YIELD_RATE = 0.20`, `HEADLINE_ACC = 0.95`.

| Condition | Verdict | Next |
|---|---|---|
| 0 ✗ AND `yield_rate ≥ 0.20` AND `top_lift > 0` | **healthy** | continue rounds 2–5 |
| ≥ 2 ✗ OR baseline regression at round 1 | **broken** | halt; full prompt revisit |
| anything else | **degraded** | halt; one-knob fix; restart |

### Diagnosis tree

| Signal | Likely cause | Fix file |
|---|---|---|
| `context_object_honored` ✗ | task_context block too low in prompt | `l1_generate.json` |
| `param_scope_discipline` ✗ | param boundary too loose, or `param_unlock_round` too low | `l1_generate.json` |
| `l2_brief_followed` ✗ | L2 brief not elevated in L1's prompt | `l1_generate.json` |
| `not_only_param_variants` ✗ | L1 only mutates node params | `l1_generate.json` |
| all ✓ + `yield_rate < 0.20` | L1 too conservative | bump `creativity` or rewrite `l1_critique.json` |
| all ✓ + `top_lift ≤ 0` | scoring or sample-set issue, not prompt | check `campaign.json::scoring`; check scoring set |
| early `lineage.source == l2_context` | `l1_critique` weak; L2 forced to fire | `l1_critique.json` |

### Sweep mode (multi-candidate screening)

When comparing N candidate L1 prompts, run each as 1 scored round + 1 generation peek instead of 5 full rounds per candidate. Promote winners to a full run.

```
1. Edit l1_generate.json (or another optimizer prompt) — candidate A.
2. python -m promptpotter optimize --sweep
   → cycle_A: baseline + 1 full round + 1 gen-only round.
3. Repeat for candidates B, C, D, ...
4. /potter-review --sweep → ranked by round_1_top_lift.
5. Promote top 2-3 to full optimize runs.
```

`proxy_lift_corr` over ≥ 4 paired (sweep, full) cycles drives the verdict: ≥ 0.6 = sweep is the primary screen; < 0.4 = suspend sweep mode.

Full M10 spec: [`../specs/m10-prompt-iteration-framework.md`](../specs/m10-prompt-iteration-framework.md).

### Bundling and generality

- One change at a time by default. Carve-out: edits targeting the same observed failure may bundle. Document the decision in `notes.md`.
- General fix, not specific. When L1 misbehaves on input X, the prompt edit guards against the *class* of mistake. Re-run to verify.
