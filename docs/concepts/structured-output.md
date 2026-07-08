# Structured output — the second prompt

The JSON Schema is serialized into the **input**. Field names, order, and `description` strings are tokens the model reads before writing anything. A schema is not a post-generation filter — it is text that conditions generation from the most privileged position available: adjacent to the slot it governs, while your task instruction sits thousands of tokens upstream competing with everything else.

Three levers, in increasing order of neglect.

**1. The name.** `rationale`, `notes`, `evidence`, `scratch` are four different requests; the model has strong priors about what belongs under each key. But a name is usually **frozen** — it is a wire contract. Downstream code reads `result["candidate"]`; validators check `field ∈ EVIDENCE_GROUNDING_FIELDS`; an n8n node branches on its `enum`. Rename it and the pipeline breaks, loudly if you are lucky. Same for the dot-path a nested field sits on, and for `enum` values: those *are* the value space something downstream switches on. **A real lever, and mostly not yours to pull.**

**2. The coordinates.** Fields are generated in schema order, and each becomes context for the next. `{answer, reasoning}` commits to the answer, then rationalizes it — fluently, which is worse than nothing, because it's now evidence you trust. `{reasoning, answer}` puts the reasoning in context before the answer is emitted. Same fields, same descriptions. Different mechanism. Nesting obeys the same rule: an inner object generates wherever its parent field sits.

*Live example.* `L1Variant` (`application/optimization/dispatch/schemas.py`) once ordered `… → *_override (the mutation) → evidence_grounding`, emitting the citation **after** the mutation it justified — so the `evidence_grounding_present` behavior check policed a symptom its own schema caused. It now generates second, above **both** the overrides and `changes_description` (itself a decision: *"the concrete change, the expected directional effect"*). `variant_name` still leads — an identifier is not a choice. The prompt's `answer_format` lists the fields in the same order; a schema that disagrees with its own prose teaches twice, contradictorily.

**3. The description.** The only natural language placed *inside* the field-filling loop. Root [`CLAUDE.md`](../../CLAUDE.md) says *never trim `Field(description=)` — LLM-facing copy*; that rule is a scar from trimming them as documentation. They are prompt.

## Which levers are actually free

| Lever | Who reads it | Safe to change? |
|---|---|---|
| Field **name** / dot-path | downstream parsers, validators, the wire | **No** — contract |
| `enum` **values** | whatever branches on the value | **No** — that *is* the value space |
| `enum` **order** + per-value gloss | nobody but the model | **Yes** |
| Field **order** | nobody but the model | **Yes** — a parsed object is unordered |
| **`description`** | nobody but the model | **Yes** — no code reads it |

An `enum` is easy to misfile. Its *values* are contract — `validators/l1_behavior.py` checks `field ∈ EVIDENCE_GROUNDING_FIELDS`, and an n8n node dispatches on its operation enum, so renaming one breaks the consumer exactly as a field rename would. What is free is the *order* the values are listed in (the model reads the first as prototypical) and any per-value gloss. Same split as everywhere else: the tokens the model reads are free; the tokens something branches on are not.

Note what this means. The lever everyone reaches for first — rename the field — is the one that breaks things. The levers invisible to every parser, and therefore free to move, are the ones nobody touches. Reordering a schema and rewriting its descriptions changes what the model produces while leaving every consumer bit-for-bit unaffected.

That asymmetry is the reason these two are the optimizable ones ([`../specs/schema-description-axis.md`](../specs/schema-description-axis.md)), and it is why a schema-mutating optimizer must be scoped to them explicitly — handed the whole schema, it will happily rename `candidate` and take the pipeline down.

## Enforcement is provider-dependent

Some providers implement structured output as **constrained decoding** — a grammar masks the token distribution, so an invalid token cannot be sampled. Others treat it as a strong suggestion and validate afterward, or not at all. Ours does both: per `VariantEvidenceGrounding`'s docstring, *"providers like Groq don't honor the enum."*

So the schema **teaches** more reliably than it **compels.** Where the grammar doesn't bind, the name, the position, and the prose are the entire mechanism — the opposite of the usual intuition.

## Shape-determinism ≠ content-determinism

Structured output is often called "quasi-deterministic." It is not. Same schema, same temperature, two calls → different values. A schema constrains **shape**; it guarantees a parseable object with the fields you named and nothing about whether the values are right.

That distinction is why this project exists. An LLM is an approximator — never *always* right, so per-call correctness is the wrong target. Shape-determinism converts unbounded generation into **bounded estimation**: prose can only be judged, a typed field can be compared, comparisons aggregate, aggregates carry an error bar, and a quantity with an error bar can be optimized. `score_search_point()`, composite fitness, θ, PoBB all rest on the answer arriving in a slot we named, positioned, and described.

A schema buys no correctness. It buys a number you can trust yourself to measure — then you go earn the correctness with a loop.
