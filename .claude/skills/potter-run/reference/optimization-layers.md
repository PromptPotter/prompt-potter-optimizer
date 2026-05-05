# Optimization Layers — L1 / L2 / L3

PromptPotter uses a 3-layer escalation model inspired by PromptWizard's critique-and-refine pattern. Each layer has a distinct responsibility and triggers only when the layer below it stalls.

## L1 Generate (every round)

The workhorse. Generates N candidate `OptSearchPoint`s (prompt field variants + pipeline_params), evaluates them against the dataset, and selects the best.

**Input**: L1 critique from previous round (or `l2_brief` after L2 runs), task_context, scan_brief, search_memory.
**Output**: winner candidate (or no improvement → stall counter increments).

After evaluation, the **L1 Critique Agent** (every-round intelligence hub) analyzes results — pipeline health, rank analysis, failure details, query categories — enriched with SearchMemory intelligence (failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends). It produces a compact L1 critique that feeds the next L1 round. L1 critique and L1 Generate are **mutually exclusive with l2_brief**: when L2 fires, its brief replaces L1 critique as L1's primary signal.

**Stall**: When L1 fails to improve for `patience` consecutive rounds → escalate to L2.

## L2 Refine Context (escalation only — on L1 stall or degradation)

Escalation-only meta-controller. Adjusts the optimization environment rather than generating candidates directly. **Only fires when L1 stalls or degradation is detected** — not during normal rounds.

**Decides**: `task_context` refinements, meta-settings (`creativity`, `n_variants`, `sample_size`), and produces an `l2_brief` (2-3 sentence diagnostic + action guidance) injected into L1's next meta-prompt.

**Does NOT decide**: `pipeline_params` — that stays L1's job.

**Input**: L1 critique, previous l2_brief (to evolve/supersede), escalation report (or warning_inventory), task_context, pipeline schema param keys, round trajectory summary, candidate comparison from last round, SearchMemory (axis rankings, bottleneck distribution, failure group × axis, persistent failures).

**Stall**: When L2 runs `l2_patience` times without L1 improving → escalate to L3.

## L3 Modify Plan (on L2 stall)

Strategic replanner. Rewrites the high-level optimization plan when both L1 and L2 have failed to find improvements.

**Decides**: new strategic `plan` that fundamentally changes what L1 explores.

**Does NOT decide**: `pipeline_params`, `task_context`.

**Input**: current plan, L2 history (last 3 rounds), rendered prompt, pipeline section, SearchMemory aggregate picture (axis rankings, bottleneck distribution, failure clusters, persistent failures).

**Stall**: When L3 runs `l3_patience` times without improvement → `l3_patience_exhausted` stop.

## Escalation Chain

```
L1 stalls (patience rounds without improvement)
    → L2 Refine Context (adjust task_context + meta-settings + l2_brief)
        → L1 resumes with l2_brief
            → still stalling?
                → L3 Modify Plan (rewrite strategy)
                    → L1 resumes with new plan
                        → still stalling?
                            → l3_patience_exhausted (stop)
```

**Mid-eval degradation (rail 2 self-healing, NOT a round-level escalation).** When a candidate's `degraded_rate >= degradation_threshold` mid-evaluation, `DegradationCheck` eliminates **just that candidate**, synthesises a `RuntimeFailure` from the check result + its observed pipeline config, and attaches it to that candidate's `OptSearchPoint.memory.runtime_failures`. The round finishes normally with the remaining candidates — the winner is never disrupted by a losing candidate's runtime issues. After the round, `execute_round` mirrors every new `RuntimeFailure` onto the outer `state.opt_sp.memory.runtime_failures` (deduped). L2 next round reads both "NEW this round" and "ACCUMULATED surviving earlier rounds" partitions and **adjusts its own strategy** — brief, task_context, optimizer_params — to re-shape L1's search around the safe region. If the pattern persists across L2 rounds, L3 `modify_plan` reads the cumulative trail and replans (change `pipeline_params`, swap nodes, rewrite `plan` text). See `docs/concepts/self-healing.md` and `docs/developer/self-healing-internals.md` for the full mechanics.

## Configuration Knobs

| Setting | Default | Purpose |
|---------|---------|---------|
| `patience` | 3 | L1 rounds without improvement before L2 |
| `enable_l2` | true | Allow L2 escalation |
| `l2_patience` | 2 | L2 invocations without improvement before L3 |
| `enable_l3` | true | Allow L3 escalation |
| `l3_patience` | 1 | L3 invocations without improvement before stop |
| `enable_l1_critique` | true | L1 critique-guided generation (vs. direct generation) |
| `degradation_threshold` | 0.4 | Mid-eval abort threshold (0 = disabled) |

## What to Tell the User

- **L2 activates**: "Optimization stalled at L1 — L2 is refining the task context and meta-settings to change what L1 explores."
- **L3 activates**: "Both L1 and L2 have stalled — L3 is rewriting the optimization strategy."
- **Escalation abort**: "Backend degradation exceeded threshold — the optimizer aborted this round and escalated to L2 to address the instability."
- **Patience exhausted**: "The optimizer tried {patience} rounds without improvement. This is normal convergence — check results to see where it peaked."
