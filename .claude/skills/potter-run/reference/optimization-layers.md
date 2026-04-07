# Optimization Layers — L1 / L2 / L3

PromptPotter uses a 3-layer escalation model inspired by PromptWizard's critique-and-refine pattern. Each layer has a distinct responsibility and triggers only when the layer below it stalls.

## L1 Generate (every round)

The workhorse. Generates N candidate `OptSearchPoint`s (prompt field variants + pipeline_params), evaluates them against the dataset, and selects the best.

**Input**: critique from previous round (or `l2_directive` after L2 runs), task_context, thinking_styles, scan_context, search_memory.
**Output**: winner candidate (or no improvement → stall counter increments).

After evaluation, the **Critique Agent** analyzes results — pipeline health, rank analysis, failure details, query categories — and produces a compact critique that feeds the next L1 round. Critique and L1 Generate are **mutually exclusive with l2_directive**: when L2 fires, its directive replaces critique as L1's primary signal.

**Stall**: When L1 fails to improve for `patience` consecutive rounds → escalate to L2.

## L2 Refine Context (on L1 stall)

Meta-controller. Adjusts the optimization environment rather than generating candidates directly.

**Decides**: `task_context` refinements, meta-settings (`creativity`, `n_variants`, `sample_size`), and produces an `l2_directive` (2-3 sentence diagnostic + action guidance) injected into L1's next meta-prompt.

**Does NOT decide**: `pipeline_params` — that stays L1's job.

**Input**: critique, previous l2_directive (to evolve/supersede), escalation report (or warning_inventory), task_context, pipeline schema param keys.

**Stall**: When L2 runs `l2_patience` times without L1 improving → escalate to L3.

## L3 Modify Plan (on L2 stall)

Strategic replanner. Rewrites the high-level optimization plan when both L1 and L2 have failed to find improvements.

**Decides**: new strategic `plan` that fundamentally changes what L1 explores.

**Does NOT decide**: `pipeline_params`, `task_context`.

**Stall**: When L3 runs `l3_patience` times without improvement → `l3_patience_exhausted` stop.

## Escalation Chain

```
L1 stalls (patience rounds without improvement)
    → L2 Refine Context (adjust task_context + meta-settings + l2_directive)
        → L1 resumes with l2_directive
            → still stalling?
                → L3 Modify Plan (rewrite strategy)
                    → L1 resumes with new plan
                        → still stalling?
                            → l3_patience_exhausted (stop)
```

**Mid-eval escalation**: `DegradationCheck` can fire during evaluation if `degraded_rate >= degradation_threshold`. This aborts remaining queries, records an `EscalationSignal`, and triggers L2 immediately. Degradation rounds don't count toward `max_rounds`.

## Configuration Knobs

| Setting | Default | Purpose |
|---------|---------|---------|
| `patience` | 3 | L1 rounds without improvement before L2 |
| `enable_l2` | true | Allow L2 escalation |
| `l2_patience` | 2 | L2 invocations without improvement before L3 |
| `enable_l3` | true | Allow L3 escalation |
| `l3_patience` | 1 | L3 invocations without improvement before stop |
| `enable_critique` | true | Critique-guided generation (vs. direct generation) |
| `degradation_threshold` | 0.4 | Mid-eval abort threshold (0 = disabled) |

## What to Tell the User

- **L2 activates**: "Optimization stalled at L1 — L2 is refining the task context and meta-settings to change what L1 explores."
- **L3 activates**: "Both L1 and L2 have stalled — L3 is rewriting the optimization strategy."
- **Escalation abort**: "Backend degradation exceeded threshold — the optimizer aborted this round and escalated to L2 to address the instability."
- **Patience exhausted**: "The optimizer tried {patience} rounds without improvement. This is normal convergence — check results to see where it peaked."
