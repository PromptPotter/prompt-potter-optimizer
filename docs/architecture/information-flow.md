# Information Flow — Optimization Loop

What gets injected into each LLM node's prompt, where it originates, and
how it gets there. Execution order is in
[`optimization.md`](optimization.md); this file owns the data.

Each bullet below reads `item (origin → retention)`. Retention is how
the data survives between production and consumption:

| Retention | Meaning |
|-----------|---------|
| `opt_sp.*` | Persisted on `OptSearchPoint` — survives across rounds in campaign trial JSON |
| `LoopState.*` | Transient within one optimization cycle |
| `config.*` | Immutable within cycle (set at campaign init or scan phase) |
| `SearchMemory` | Cross-campaign materialized view, queried at format time |
| *(ephemeral)* | Computed and consumed within a single round, not stored |

---

## L1 Generate — inbox

Fires every round.

- **Rendered prompt** (`opt_sp.render()` → *(ephemeral)*) — full template with the 8 prompt-scheme fields.
- **L1 guidance — critique text XOR l2_directive** (`CritiqueAgent.run()` → `opt_sp.memory.critique_text`; `refine_strategy()` → `opt_sp.memory.l2_directive`). Mutual exclusion is enforced in `format_context_sections()` at the `if/elif` junction: when both are populated, the directive wins because L2 digests the critique when it fires. Directive is cleared by `clear_volatile()` on improvement; critique regenerates every round.
- **Task context** (`opt_sp.task_context`) — read-only from L1's view (L2 owns edits).
- **Thinking styles** (`sample_thinking_styles()` → `opt_sp.memory.thinking_styles`) — 3 sampled.
- **Plan** (`opt_sp.plan`) — read-only from L1's view (L3 owns edits).
- **Failure analysis** (`compile_failure_analysis()` → `LoopState.failure_analysis`) — top 3 clustered failure patterns with example queries and signals.
- **SearchMemory digest** (`SearchMemory.to_l1_digest()` → *(ephemeral)*) — failure clusters, dead queries, top axes, best-performing values.
- **Probe-round-only extras** (`opt_sp.memory.warning_inventory` + `opt_sp.memory.escalation_journal`) — per-query warning breakdown + recent step attempts. L1 sees these only when `state.probe_next_round` is set (L2 requested a targeted probe).

---

## Critique — inbox

Fires every round. Sole reader of raw eval results; acts as the every-round intelligence hub.

- **Eval results (raw)** (`score_search_point()` → *(ephemeral)*) — sole reader. Per-query hit/miss, diagnostics, warnings, term-matching detail.
- **Round history** (`LoopState.rounds`) — accuracy trajectory + per-round pipeline_params for trend framing.
- **Pipeline schema** (`config.pipeline_schema`) — `candidate_keys` only, derived from ranker / candidate_source nodes.
- **SearchMemory digest** (`SearchMemory.to_critique_digest()`) — discriminating queries, tractability profiles, axis exhaustion, value trends, failure clusters.

---

## L2 Refine — inbox

Fires on escalation only (stall, degradation). L2 is a strategic meta-controller: it owns `task_context`, meta-settings, and the `l2_directive`; it does NOT set pipeline_params.

- **Previous critique text** (`opt_sp.memory.critique_text`) — L2 builds on it, then its directive absorbs it for L1's next round.
- **Previous L2 directive** (`opt_sp.memory.l2_directive`) — evolves or supersedes it.
- **Escalation journal** (`opt_sp.memory.escalation_journal`) — full history of degradation events (round, problem_step, step_config, outcome).
- **Escalation report XOR warning inventory** (`format_escalation_report()` OR `opt_sp.memory.warning_inventory`) — mutual exclusion: L2 sees the aggregated stability report when escalation fires, else the per-query warning breakdown. The report already contains aggregate warning counts.
- **Task context + optimizer params** (`opt_sp.task_context`, `opt_sp.optimizer_params`) — editable; L2's primary knobs are `creativity`, `n_variants`, `sp_budget_ttest`.
- **Pipeline schema via escalation** (`config.pipeline_schema`) — surfaced only through the escalation report's "problem step available parameters" line. L2 does not see the full pipeline parameter listing; that's L1's domain.
- **Validation failures** (`opt_sp.memory.validation_failures`) — parse-time invariant violations from L1's last round. Rail 1 input: L2's job is to teach L1 by naming the disallowed value in the next directive.
- **Runtime failures** (`opt_sp.memory.runtime_failures`) — mid-eval `DegradationCheck` records. `format_l2_intelligence()` partitions this single list at format time by `first_seen_round == current_round` into NEW (this round) vs ACCUMULATED (surviving earlier rounds). Rail 2 input: L2's job is to adjust its OWN strategy — tighten directive, refine task_context, narrow optimizer_params.
- **Trajectory + candidate comparison** (`build_trajectory_report()`, `build_candidate_comparison()` → *(ephemeral)*) — classification (healthy / plateau / oscillating / ceiling) and per-candidate accuracy diff.
- **SearchMemory digest** (`SearchMemory.to_strategic_digest(include_correlations=True)`) — axis rankings, bottleneck distribution, failure group × axis correlations, persistent failures.

---

## L3 Plan — inbox

Fires when L2 stalls. L3 owns the strategic plan and may propose pipeline_params deltas.

- **Rendered prompt + current plan** (`opt_sp.render()`, `opt_sp.plan`) — plan is editable.
- **L2 history** (last 3 rounds from `state.escalation` → *(ephemeral)*) — `{l2_round, params, acc_change}` triples.
- **Pipeline schema** (`config.pipeline_schema`) — full per-node parameter keys (contrast with L2's escalation-report-only view).
- **Runtime failures** (`opt_sp.memory.runtime_failures`) — the L2→L3 escalation trail. `format_runtime_failures_for_l3()` renders these as "patterns L2 couldn't reduce; replan required." L3 treats them as discovered constraints: change pipeline_params, swap nodes, or rewrite plan text to escape the failing region.
- **SearchMemory digest** (`SearchMemory.to_strategic_digest(include_clusters=True)`) — axis rankings, bottleneck distribution, failure clusters, persistent failures.

---

## Self-Healing Rails

Two independent flows use the above inboxes:

**Rail 1 — Validation failures.** L1 parse-time invariant check (`validate_overrides()`) finds a proposed value outside the allowed set. The candidate short-circuits to synthetic 0; the failure lands on its `OptSearchPoint.memory.validation_failures`. L2 next round receives it in the "L1 VALIDATION FAILURES" section and produces a directive that names the disallowed value by name. L1 heals on the following round via the directive/critique mutual exclusion. L1 never sees the raw failure.

**Rail 2 — Runtime failures.** `DegradationCheck` fires mid-evaluation when `degraded_rate ≥ threshold`. `_score_candidates()` synthesises a `RuntimeFailure` (now carrying `first_seen_round` and `candidate_label`) attached to the failing candidate's `memory.runtime_failures`. End-of-round, `execute_round` mirrors new failures onto the outer `state.opt_sp.memory.runtime_failures`, deduped by `(source, dominant_warning, observed_config)`. L2 then reads only outer memory and partitions at format time — NEW vs ACCUMULATED is a display view, not two separate data paths. L2 heals itself by adjusting directive / task_context / optimizer_params. When ACCUMULATED patterns keep growing across L2 rounds, L3 receives the trail via `format_runtime_failures_for_l3()` and must replan the pipeline itself.

Rails differ in **who heals** and **what the healing action is**: rail 1 is L1 self-healing via L2 directive; rail 2 is L2 self-healing with L3 escalation.

---

## Internal (not a prompt injection)

**Stale data observations** — accumulated during eval → `opt_sp` → aggregated cross-campaign by SearchMemory. Consumed by `l1_evaluate`'s stale data protocol (`sampleswitch` queries SearchMemory's per-query degradation rate). Never enters an LLM prompt.

---

## Compression Chain

```
eval results ──► Critique (LLM) ──► critique_text ──► L2 (LLM) ──► l2_directive ──► L1 (LLM)
                 1st hop                               2nd hop
```

When L2 fires, L1 is 2 LLM hops from eval data. Each hop is lossy compression. The directive/critique mutual exclusion ensures L1 gets the most processed form available. Round trajectory and failure group insights will feed L2 directly (bypassing this chain), so L2 can produce better-informed directives for L1.

Validation failures bypass critique entirely and feed L2 directly (1 hop instead of 2) — the signal is already structured, no eval-result digestion needed.

L3 sees only `l2_summary` (last 3 L2 outcomes: what changed, whether accuracy moved) — never L2's directive or reasoning. Strategy from outcomes, not tactics.

---

## Self-Optimization (Meta-Level)

The potter's own prompts are themselves `PromptTemplate` instances — the 8-field decomposition applies recursively, which is what enables a future self-optimization mode. The trace dataset (`potter_traces` loader) freezes archived campaign transitions into `{round_context → next_directive → score_delta}` rows that an outer-loop PromptPotter instance can score meta-prompt variants against. See [prompt-scheme.md § Optimizer Meta-Prompts](prompt-scheme.md#optimizer-meta-prompts) and [§ Potter Trace Dataset](prompt-scheme.md#potter-trace-dataset).
