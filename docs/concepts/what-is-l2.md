# What L2 Is

L2 is the optimizer's strategist. It does not write prompts; it shapes what the prompt-writer (L1) sees, knows, and is allowed to do. L2 fires only when L1 has stalled — it is escalation-driven, not every-round. On healthy tasks where L1 keeps improving, L2 may stay dormant for many rounds (or for a whole campaign).

This page is for operators. For the implementation, see [`../developer/l2-internals.md`](../developer/l2-internals.md).

---

## When L2 fires

After every L1 round, the runner checks whether the round improved best accuracy. If it did, L2 stays out — L1 is doing fine on its own. If the round didn't improve, an L1 stall counter ticks; once it reaches `l1_patience` consecutive stalls, L2 fires for the next round.

L1's defaults already cover most non-trivial tasks well, so L1 often goes several rounds in a row without help. L2 is the rescue layer that engages when L1 has exhausted its near-term moves.

## What L2 watches when it fires

When L2 runs, the runner hands it a snapshot of what's happened so far:

- The accuracy of the round winner.
- Failure clusters from the critique step.
- Validation failures (L1 proposed a value outside the allowed set — e.g. a model that's not on the allowlist).
- Runtime failures (L1's candidate ran but the pipeline raised warnings — e.g. reasoning budget exhausted).
- The axis-index digest — which knobs have been tried, what helped, what didn't.
- The current state of L1's surface — every section that's currently visible to L1 and any text overrides L2 placed there in earlier fires.

L2 reads all of that, writes whichever fields it wants onto the optimizer's state record (the *individual* — see [optsearchpoint-as-state.md](optsearchpoint-as-state.md)), and stops.

## What L2 can mutate

L2's output is a flat dict; every field is independent. L2 sets only the fields it wants to change and leaves the rest unset.

| Field | What it does |
|-------|--------------|
| `directive` | A 2-3 sentence strategic note injected into L1's next prompt as primary signal. |
| `optimizer_params` | Tune creativity, candidate budget, variant strategy. |
| `task_context` | Refine the structured domain understanding (domain, pipeline purpose, data characteristics, key challenges). |
| `scheme_overrides` | Per-section visibility toggles for L1's surface — `{section: false}` hides a section, `{section: true}` re-enables one. |
| `text_overrides` | Per-section text replacements for L1's surface — substitute hand-written content for the auto-generated text of a section. |
| `template_override` | Replace the whole `problem_description` body of L1's prompt template with a custom version. Reserve for fundamental reframing. |
| `action` | `normal_round` (full scoring set) or `probe_round` (warned queries only). |

`scheme_overrides`, `text_overrides`, and `template_override` are L2's levers over L1's prompt surface — see [l1-generate-surface.md](l1-generate-surface.md) for the closed catalogue they target.

## A worked example

A TermNorm campaign stalls at 60% accuracy for `l1_patience` rounds. L2 fires. The critique flags two failure clusters:

1. Queries with abbreviation-style ground truths missing systematically.
2. The model occasionally proposes `gpt-4o` even though it's not on the allowed model list.

L2 writes:

```json
{
  "action": "normal_round",
  "directive": "Two recurring failures: (1) abbreviation-form ground truths are missed — instruct candidates to consider expansion variants in entity_profiling; (2) the model gpt-4o is not on the allowed list — restrict model overrides to the configured allowlist only.",
  "rationale": "Failure clusters point at the instruction axis."
}
```

That directive flows into L1's next round as primary guidance. L1's job is to translate the directive into specific candidate values; L2's job was to name the failures and direct attention.

## Why surface mutations matter

Two scenarios they prevent:

1. **Silent capability loss.** If a future automation (the L4 meta-learner) edits L1's prompt template and accidentally drops a section, the optimizer would forever lose that signal. The catalogue is code-authoritative; the only way to drop a section is to delete its enum entry — a deliberate code change.
2. **Misleading sections.** If a section is currently firing on a non-issue and confusing L1's variants, L2 can gate it off via `scheme_overrides`. The override persists until L2 (or L3) flips it back.

L2 writing onto the OSP state record fixes both. State persists across rounds. The closed catalogue of L1-surface sections is code-authoritative — see [l1-generate-surface.md](l1-generate-surface.md).

## See also

- [l2-decision-tree.md](l2-decision-tree.md) — what L2 typically writes in different scenarios.
- [l1-generate-surface.md](l1-generate-surface.md) — the catalogue of variables L1 sees, and L2's three levers over it.
- [optsearchpoint-as-state.md](optsearchpoint-as-state.md) — the individual record L2 writes to.
- [three-layer-loop.md](three-layer-loop.md) — how L2 fits between L1 and L3.
