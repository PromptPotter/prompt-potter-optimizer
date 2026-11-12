# Structured output — the second prompt

The JSON Schema is serialized into the **input**: field names, order and `description` strings are tokens the
model reads before writing anything, adjacent to the slot they govern, while the task instruction sits
thousands of tokens upstream. Three levers:

1. **The name.** `rationale`, `notes`, `evidence`, `scratch` are four different requests — but a name is a
   wire contract, and so are a nested dot-path and `enum` values: something downstream switches on them.
2. **The coordinates.** Fields are generated in schema order, each context for the next: `{answer, reasoning}`
   commits, then rationalizes; `{reasoning, answer}` puts the reasoning in context first. Nesting obeys the
   same rule. *Live example:* `L1Variant` (`application/optimizers/potter/dispatch/schemas.py`) generates
   `evidence_grounding` **before** the delta slots it justifies. The required set binds as hard as the order:
   the payload generates before the prose that reports it (`changes_description`), a variant that changes
   nothing against its parent is rejected as `no_op_variant` by `validators.py` (over
   `domain/candidate_diff.py::candidate_delta`) — never at parse, where one rejection costs the whole batch —
   and no unbounded free-text identifier leads the object. The prompt's `answer_format` lists the fields in
   the same order.
3. **The description.** The only natural language inside the field-filling loop;
   [`../developer/conventions.md`](../developer/conventions.md) § Prose in the source keeps
   `Field(description=)` out of the docstring rules for this reason. They are prompt.

## A place to think is part of the ask

A schema of `{answer}` alone removes the room to derive one. Two channels, both captured: a `reasoning` field
on a node's `output_schema`, ordered first; and the provider's native `message.reasoning`, captured as
`LLMResponse.reasoning`. **Both are strictly analytical** — they ride the ledger to the audit twin and the
node-detail pane and never reach a gate, metric, validator, scorer or cache key, or the loop learns to narrate.
Neither has a code reader, and neither is dead (`infrastructure/llm/response.py`'s field note).

## Which levers are actually free

| Lever | Who reads it | Safe to change? |
|---|---|---|
| Field **name** / dot-path | downstream parsers, validators, the wire | **No** — contract |
| Field **type** | the parser, every consumer of the parsed value | **No** — contract |
| `enum` **values** | whatever branches on the value | **No** — that *is* the value space |
| `enum` **order** + per-value gloss | nobody but the model | **Yes** |
| Field **order** | nobody but the model | **Yes** — a parsed object is unordered |
| **`description`** | nobody but the model | **Yes** |

**What the optimizer may reach.** `description` is open by default — no campaign toggle, because a toggle on
a free lever is a fallback chain wearing a flag: any node that ships an `output_schema` gets one param per
field, `output_schema_descriptions.<path>`, an ordinary target-level param that locks like any other. The field
**name** is the one contract lever the optimizer may pull, only after an L2/L3 fork opens `l1_generate`'s
`schema_field_rename` (off in potter's manifest): a rename is a presentation transform — the emitted schema
advertises the new name, a `validation_alias` binds it back. **Type, `enum` values and the schema itself have
no toggle**: `SCHEMA_OWNED_FIELDS` (`domain/pipeline_schema.py`) subtracts `output_schema` / `schema_family` /
`schema_version` / `answer_fields` from every emittable param surface. `never_axis` says who may SEARCH a key,
never who may set it, so `answer_fields` stays settable on a fork. The schema is a tree the "Structured
output" view renders and a check-in authors field by field.

**Whether the node uses a schema AT ALL is ours.** `response_format` is synthesized onto every LLM node at
parse time (`SCHEMA_TOGGLE_PARAM`), because PromptPotter puts the schema on the wire: `json` sends
`output_schema` + `answer_fields`; `text` sends neither, and the answer is found by the matcher's own contract
(its `formula/matchers.py::MATCHERS` entry's `note`). Three pins ride the value space: a node declaring no
schema, a node graded on several slots (pinned to `json`), a model that does not accept the key
(`PipelineSchema._refused`). Why a connector must not declare it too —
[`../developer/node-standard.md`](../developer/node-standard.md) § The shape, and what PromptPotter reads of it.

*An enum whose value space is per-call belongs on the wire schema, not on the model.* `evidence_grounding.field`
is grafted per round in `build_l1_response_schema` from `citable_fields(layout, …)`, so a citable name the
prompt never rendered is unrepresentable; a frozen copy in a model would silently diverge. A schema-mutating
optimizer is scoped to the free levers explicitly.

## Enforcement is provider-dependent

Some providers implement structured output as constrained decoding; others validate afterward, or not at
all. The schema **teaches** more reliably than it **compels**, so `VariantEvidenceGrounding`'s validator
enforces the grafted enum. Two rules for every optimizer wire: **no prose field carries a `maxLength`** —
where the grammar binds, every answer ends mid-sentence at the cap, unmarked; the prompt asks for the short
form and the parse boundary cuts with a visible mark (`schemas.py::_truncate_marked`) or rejects
(`validators.py::l1_prose_within_ceiling`). **A value the loop must be able to refuse is one a check can read
off the prompt** — a panel name, a row id (`L1Variant.targets_rows`) — never free text closed by a grafted enum.

## Extraction is a double seam — slot first, label second

The **backend** destructures the structured-output SLOTS named by `answer_fields` before the body is posted
back — one slot as its value, several as the JSON object of exactly them. The **matcher**
(`application/scoring/formula/matchers.py`) pulls the LABEL out of what is left and decides HIT/MISS; no
matcher sees the raw envelope, and one digging a field out of a whole response is fixing the wrong seam.
Display-side extraction is a third thing (`application/views/render/sample.py`).

## Several graded fields — one item per call

A node is optimized as a whole when `answer_fields` names several slots and the per-sample formula is
`fields_match(predicted, ground_truth)`: the gold is `{field: [accepted, …]}` (`""` "not stated", `null` a field
the row does not grade), each field matched alone under one normalization, the cell's fitness the share of its
graded fields right. **The share, never one item per field**: θ is fit on graded responses in [0, 1] with a
Pearson dispersion (`intelligence/rasch.py::_dispersion`); splitting a sample into (sample × field) items would
treat K verdicts from ONE generation as independent, understating every θ SE and firing PoBB's cuts early.
`hit` stays all-fields-right (`HIT_THRESHOLD` is 1.0); per-field verdicts ride `Grade.field_hits` into
`CellFold.field_accuracy`, which the readout prints per arm and per round.

## Shape-determinism ≠ content-determinism

Same schema, same temperature, two calls → different values. A schema constrains **shape**: a parseable object
with the fields named, nothing about whether the values are right. What shape buys is bounded estimation — a
typed field can be compared, and comparisons aggregate with an error bar.
