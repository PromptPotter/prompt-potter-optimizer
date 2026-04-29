# L2's Decision Tree

When L2 fires (on L1 stall), it picks which fields to write on the next OSP. Every field is independent — L2 can write any combination, or write nothing at all. This page shows the typical scenarios.

For the implementation, see [`../developer/l2-internals.md`](../developer/l2-internals.md).

---

## Default — write a directive

The most common L2 fire. The critique flags a clear failure pattern; L2 writes a 2-3 sentence directive that names the axis and the direction. L1's next round reads the directive as primary signal.

> Stall at 82%. Critique flags one failure cluster: 30% of misses are queries where the model returns "I don't know" instead of a guess.
>
> L2 writes: *directive = "Failure cluster: model is producing 'I don't know' on 30% of misses. Generate variants whose `instruction` field forces a guess when uncertain — use language like 'always commit to one answer'."*

## Stay quiet

L2 fires because L1 hasn't improved for `l1_patience` rounds, but the data does not yet support a specific direction — the failures look noisy, the axis digest does not point at one knob, no validation or runtime failures have appeared. L2 writes nothing (or just an empty `rationale`). The next round runs with the OSP unchanged. This burns one L2 fire but is honest — a guess directive would just churn the search.

## Write a directive plus tune optimizer params

The failure pattern is named, but the search is also too narrow (or too wide). L2 writes a directive AND adjusts `optimizer_params` to widen / narrow exploration.

> Stall at 88%. Critique flags one failure cluster, but only two of the last three rounds proposed candidates that varied in the relevant axis. The candidate budget is set to 3.
>
> L2 writes: *directive = "..."*, *optimizer_params = {creativity: 0.5, n_variants: 5}*.

## Call a probe round

One narrow failure mode dominates and L2 has a hypothesis to test. The full scoring set adds noise; the smaller warned-query subset gives a cleaner signal.

> Stall at 87%. Runtime warnings dominated by `web_search:no_results` on the same six queries every round. Axis digest shows `web_search.max_sites` was tried at 3, 5, and 7 with the same warning rate.
>
> L2 writes: *action = "probe_round"*, *directive = "Test whether `web_search.engine` matters on the persistent web_search:no_results queries."*

The next round scopes evaluation to those six queries.

## Toggle a misleading section off

A section in L1's prompt is currently firing on a non-issue and pulling L1's variants away from the actual failure cluster.

> The `escalation_alert` section is firing on a single degraded query (no real pattern). L1's last three rounds all proposed pipeline-robustness variants instead of addressing the abbreviation cluster from the critique.
>
> L2 writes: *scheme_overrides = {"escalation_alert": false}*, *directive = "Address the abbreviation cluster — pipeline is healthy."*

The section stays off until L2 flips it back.

## Replace a section's text

A section is currently empty (or generic) and a hand-written substitute would help. L2 writes that substitute as a `text_overrides` entry — the override persists across rounds.

> `task_context` is sparse — the original decomposition produced little. L2 has now seen enough evidence to articulate the domain explicitly.
>
> L2 writes: *text_overrides = {"task_context": "Domain: medical billing codes. Ground truths use ICD-10-CM format..."}*.

## Replace L1's whole prompt body

The framing is fundamentally wrong — multiple rounds of L2 directives have not moved the needle, and the axis digest shows L1 has tried every prompt-field axis. L2 writes a `template_override` that reframes the problem.

> Stall at 60% for four L2 fires. Each fire wrote a slightly different directive; nothing improved. The current `problem_description` body assumes a retrieval-style pipeline but the recent failure pattern is reasoning-style.
>
> L2 writes: *template_override = "<reasoning-framed body containing {{l2_directive}} hole>"*, *directive = "..."*.

This is a large mutation; reserve it for cases where the framing itself is wrong.

## How L2 picks

L2's prompt receives:

1. The current optimizer params and task context.
2. A code-derived **catalogue** of every section currently in L1's prompt — name, on/off state, override text if any.
3. The same analysis sections L1 receives (failure clusters, axis digest, runtime failures, etc.).

L2's output is a flat JSON dict; only the fields it wants to change need to be set. See [`../developer/l2-internals.md`](../developer/l2-internals.md) for the parser contract.

## See also

- [what-is-l2.md](what-is-l2.md) — what L2 is and what it watches.
- [l1-generate-surface.md](l1-generate-surface.md) — the surface L2's section overrides operate on.
- [self-healing.md](self-healing.md) — how L2's directives carry the validation + runtime healing loops (Loops 1 and 2).
