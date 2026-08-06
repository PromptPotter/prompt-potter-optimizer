# Structured output — the second prompt

The JSON Schema is serialized into the **input**. Field names, order, and `description` strings are tokens the model reads before writing anything. A schema is not a post-generation filter — it is text that conditions generation from the most privileged position available: adjacent to the slot it governs, while your task instruction sits thousands of tokens upstream competing with everything else.

Three levers, in increasing order of neglect.

**1. The name.** `rationale`, `notes`, `evidence`, `scratch` are four different requests; the model has strong priors about what belongs under each key. But a name is usually **frozen** — it is a wire contract. Downstream code reads `result["candidate"]`; validators branch on `evidence_grounding.field`; an n8n node branches on its `enum`. Rename it and the pipeline breaks, loudly if you are lucky. Same for the dot-path a nested field sits on, and for `enum` values: those *are* the value space something downstream switches on. **A real lever, and mostly not yours to pull.**

**2. The coordinates.** Fields are generated in schema order, and each becomes context for the next. `{answer, reasoning}` commits to the answer, then rationalizes it — fluently, which is worse than nothing, because it's now evidence you trust. `{reasoning, answer}` puts the reasoning in context before the answer is emitted. Same fields, same descriptions. Different mechanism. Nesting obeys the same rule: an inner object generates wherever its parent field sits.

*Live example.* `L1Variant` (`application/optimization/dispatch/schemas.py`) generates `evidence_grounding` **before** the `*_override` it justifies. Emitted after, a citation can only rationalize a mutation already made — and the `evidence_grounding_present` check would police a symptom its own schema caused.

The second correction was costlier, and it was in the **required set**, not the order alone: `changes_description` was required and generated *above* the three override slots, which were optional. A model could therefore name a change, cite a panel for it, and mutate nothing — and roughly one live variant in ten did exactly that, arriving narrated-but-empty. The schema was asking for the story and treating the substance as garnish. The payload now generates before the prose that reports it, at least one override is enforced at parse (`_reject_empty_mutation`), and `variant_name` — an unbounded free-text identifier that led the object, that no engine reader consumed, and that the decoder repeatedly derailed into repetition loops — is deleted outright. The prompt's `answer_format` lists the fields in the same order; a schema that disagrees with its own prose teaches twice, contradictorily.

**3. The description.** The only natural language placed *inside* the field-filling loop. Root [`CLAUDE.md`](../../CLAUDE.md) says *never trim `Field(description=)` — LLM-facing copy*; that rule is a scar from trimming them as documentation. They are prompt.

## A place to think is part of the ask

Before any of those levers: does the model have **anywhere to put its reasoning**? A schema of `{answer}` alone does not just omit the rationale — it removes the room to derive one, and the model answers straight from the prompt. Hand it a bare classification slot and you get a label with nothing behind it. `{reasoning, answer}` is not instrumentation bolted onto `{answer}`; it is a different, better request, which is why lever 2 above is about order and this is about *existence*.

Two mechanisms, one principle, and both are captured:

- **The schema slot** — a `reasoning` field on a node's `output_schema` (justlogic's `{reasoning, answer}`). Ordered first, so thinking is in context before the answer commits.
- **The provider's native channel** — `message.reasoning` on the OpenAI-compat wire, captured as `LLMResponse.reasoning` for reasoning models.

**Both are strictly analytical.** They ride the ledger to the audit twin and the operator's node-detail pane, and they never reach a gate, metric, validator, scorer or cache key — score a model's narration of its work instead of its work, and the loop learns to narrate. The corollary that catches people: **neither has a code reader, and neither is dead.** `LLMResponse.reasoning` has been proposed for deletion by a dead-surface audit; its field note in `infrastructure/llm/response.py` is the standing answer. Do not remove a thinking channel because nothing branches on it — nothing branching on it is the design.

## Which levers are actually free

| Lever | Who reads it | Safe to change? |
|---|---|---|
| Field **name** / dot-path | downstream parsers, validators, the wire | **No** — contract |
| Field **type** | the parser, and every consumer of the parsed value | **No** — contract |
| `enum` **values** | whatever branches on the value | **No** — that *is* the value space |
| `enum` **order** + per-value gloss | nobody but the model | **Yes** |
| Field **order** | nobody but the model | **Yes** — a parsed object is unordered |
| **`description`** | nobody but the model | **Yes** — no code reads it |

**What the optimizer may reach, and how it is stopped.** `description` is free and always on — no toggle, because a toggle on a free lever is a fallback chain wearing a flag. It is a **core, target-level** lever: any pipeline node that ships an `output_schema` gets `output_schema_descriptions` synthesized onto its tunable surface at parse time (`pipeline_parsing.py`), emitted keyed by that node's own fields (`build_l1_response_schema`), and folded into the wire schema's prose at `OptSearchPoint.to_job_search_point` (`fold_schema_descriptions`) — so `l1_generate` rewriting `llm_only`'s `{reasoning, answer}` descriptions is the ordinary case, not an L4 special case. The field **name** is the one contract lever the optimizer may pull, and only after an L2/L3 fork opens `optimization.schema_field_rename` (off by default); it is safe there because a rename is a presentation transform — the emitted schema advertises the new name, a `validation_alias` binds it back, and no downstream reader observes it. **Type, `enum` values, and the schema itself have no toggle at all**: `SCHEMA_OWNED_FIELDS` (`domain/pipeline_schema.py`) subtracts `output_schema` / `schema_family` / `schema_version` / `answer_field` from every emittable param surface, so the LLM cannot emit a key that does not exist. Structural, not policed per round.

An `enum` is easy to misfile. Its *values* are contract — `evidence_grounding_present` rejects a `field` outside the round's citable set, and an n8n node dispatches on its operation enum, so renaming one breaks the consumer exactly as a field rename would. What is free is the *order* the values are listed in (the model reads the first as prototypical) and any per-value gloss. Same split as everywhere else: the tokens the model reads are free; the tokens something branches on are not.

*Corollary — an enum whose value space is per-call belongs on the wire schema, not on the model.* `evidence_grounding.field` is grafted per round in `build_l1_response_schema` from `citable_fields(layout, …)`, the same call that fills the prompt's menu, so a citable name the prompt never rendered is unrepresentable. **If a value space depends on state, freezing it in a model is a second declaration that will silently diverge from the first** — and the validator checking the frozen copy waves the fabrications through.

Note what this means. The lever everyone reaches for first — rename the field — is the one that breaks things. The levers invisible to every parser, and therefore free to move, are the ones nobody touches. Reordering a schema and rewriting its descriptions changes what the model produces while leaving every consumer bit-for-bit unaffected.

That asymmetry is the reason these two are the optimizable ones ([`../specs/schema-description-axis.md`](../specs/schema-description-axis.md)), and it is why a schema-mutating optimizer must be scoped to them explicitly — handed the whole schema, it will happily rename `candidate` and take the pipeline down.

## Enforcement is provider-dependent

Some providers implement structured output as **constrained decoding** — a grammar masks the token distribution, so an invalid token cannot be sampled. Others treat it as a strong suggestion and validate afterward, or not at all. Ours does both: per `VariantEvidenceGrounding`'s docstring, *"providers like Groq don't honor the enum."*

So the schema **teaches** more reliably than it **compels.** Where the grammar doesn't bind, the name, the position, and the prose are the entire mechanism — the opposite of the usual intuition.

## Shape-determinism ≠ content-determinism

Structured output is often called "quasi-deterministic." It is not. Same schema, same temperature, two calls → different values. A schema constrains **shape**; it guarantees a parseable object with the fields you named and nothing about whether the values are right.

That distinction is why this project exists. An LLM is an approximator — never *always* right, so per-call correctness is the wrong target. Shape-determinism converts unbounded generation into **bounded estimation**: prose can only be judged, a typed field can be compared, comparisons aggregate, aggregates carry an error bar, and a quantity with an error bar can be optimized. `score_search_point()`, composite fitness, θ, PoBB all rest on the answer arriving in a slot we named, positioned, and described.

A schema buys no correctness. It buys a number you can trust yourself to measure — then you go earn the correctness with a loop.
