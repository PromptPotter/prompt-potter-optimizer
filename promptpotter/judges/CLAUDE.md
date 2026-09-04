# judges/ — LLM-as-judge graders for scoring

A judge grades one cell by asking a model, where no deterministic matcher can — a free-text
answer, a CRM note, a rated ordering. Adding one is local to `judges/<name>.py` plus a
`_BUILTIN` row, exactly as adding a connector is.

## A judge is a MEASUREMENT, never a formula term

**This is the rule the whole package is shaped around, and it is a hard constraint rather than a
preference.** The compiled scoring formula is pure, synchronous and AST-allowlisted, and it is
re-run **offline over already-archived rows** at six sites: the δ ruler / axis index
(`intelligence/indexes/axis.py`), A/B replay, resume, exploration, the origin gate, and the
hard-sample archive. `domain/scoring.py` states it outright — *"Archive rows are RE-GRADED by the
reading campaign's scorer … a STORED verdict is never consulted."*

So a judge inside the formula would re-bill **one LLM call per archived row, every time an index
warms**. Instead a judge runs ONCE, at measure time, and banks its verdict into the row; the pure
formula reads that banked number by name. Re-grading stays free, which is what makes `--from N`,
`--fork-on-divergence` and `verify` cheap.

`compiler.py::_refuse_label_formula` already names the pattern for the connector case: *"Score the
observation the backend emits instead."* A judge is that sentence with a different producer.

## The seam — a judge IS an `Evaluator`

No new concept reaches the scoring layer. `build_evaluators(specs)` returns ordinary
`application/scoring/evaluators.py::Evaluator`s at `per_sample` scope — one per declared TERM —
and four of their existing fields already do the work:

| field | what it buys |
|---|---|
| `scope="per_sample"` | materialized inside `measure_sample`, once, at measure time |
| `needs_labels=True` | auto-skipped on a verifier-graded bank — a judge never fabricates a 0.0 where there is no gold |
| `from_rows=False` | `materialize_row_derivable` never recomputes it — the anti-re-bill property, already encoded |
| `direction` | polarity, which of the whole surveyed ecosystem only Arize Phoenix has |

`materialize_sample_values` is **async, and `per_sample` only**. `_validate_evaluator` REFUSES an
awaitable `compute` at `per_round` scope, because those materializers are sync read paths over
archived rows — that refusal is what stops the re-billing bug being reintroduced from the sync
side. The values land **top-level in `pipeline_data`**, where `cell_namespace`'s splat binds them;
nested under a dict they would be unreachable, since the AST allowlist bans attribute access.

**Every judge evaluator goes through `validate_campaign_evaluator`, and that call is the contract
rather than a courtesy.** The rules were written as if they already covered a judge and covered
only `_REGISTRY`, so the one evaluator name an OPERATOR picks — a judge's term — was the one
nothing checked. Three ways a term is unreachable or wrong, all silent: a non-identifier the
formula's AST allowlist cannot resolve, a name `cell_namespace` binds itself (dropped by the splat,
so the formula scores the intrinsic), and a name a package evaluator owns (`extra` is written
last, so the formula scores the judge under a name promising something else).

## Scoring, never the optimizer loop — and it is structural

**Nothing that sets the loop's LLMs may reach a judge.** `allowed_models`,
`nodes.{node}.config.model`, the optimizer's own `assets/optimizer/pipeline.yaml`, and any global
"set every model" steer are all not consulted. A judge's models are declared in
`campaign.yaml::campaign_config.judges.{term}.stages` and inherited from nowhere — not from the
loop, and not from a sibling term either; absent means absent, and never a borrow.

Three things enforce it rather than describe it: `JudgeStage.model`/`provider` are **required**
fields with no default; the judge sits outside `param_keys` entirely, so the optimizer can never
search its own grader (a candidate free to move its ruler would optimize the ruler); and
`TokenUsageKind` carries a third arm `"judge"` so grading spend lands in its own
`SpendRollup` bucket instead of reading as optimizer cost.

## A judge is a COMPOSITION, not a call

`JudgeSpec.stages` is a LIST. Two models in a row (grade, then tie-break) or several in parallel
(a panel, voted) are the same type — one stage is the common case, never the assumed one.
`Judge.grade` returns one `JudgeVerdict`; how many calls it made is its own business and no part
of the scoring seam knows. Token accounting is per underlying call via `call.py::ask`, so a
two-model chain prices correctly with no special case.

`call.py::graded` is the other half of that: `ask` plus the verdict shaping every judge repeats,
in one place because its two absence arms are not formatting. **A grader that FAILED must never be
bankable as a graded answer** — an unreachable model and an unparseable reply both return
`score=None`, and a judge writing its own copy of that is one edit from defaulting to a category
instead, which is precisely the upstream behaviour `simpleqa.py` documents diverging from.

## The step schema — `retrieve → ground → answer`

`campaign_config.judges` is keyed by **the term the scoring formula reads, never by the judge's
name**, and that is what makes a multi-STEP cell expressible: three entries, three rubrics, three
banked observations per cell. Keyed by judge, two terms sharing a rubric would collapse into one
and the second verdict would land on top of the first.

**The schema is `retrieve → ground → answer`, it is a semantic decision, and it is fixed BEFORE a
cell is bought.** Per-step δ pools only if "step 2" is the same KIND of thing across cells, so a
turn *index* is not an item and an agentic episode takes however many turns it takes. Retrofitting
a schema means re-paying for every row — the fingerprint folds the whole term → judge mapping
(`pipeline_resolve.py::_identity_contributions`), so re-keying a grader is a new measurement, by
construction. Declaration order is the step order; nothing reads it yet, and what a later testlet
or partial-credit fit reads is the banked terms, not a re-measure
([`../../docs/methods/verdict-resolution.md`](../../docs/methods/verdict-resolution.md) § Phase 3).

Three steps, and which half of a failure each isolates:

| step | graded by | reads | needs gold | what it separates |
|---|---|---|---|---|
| retrieve | `evidence_retrieval` | question, `reasoning_trace` | no | did the system gather evidence that SETTLES the question — whether or not it then used it |
| ground | `answer_grounding` | question, answer, `reasoning_trace` | no | is the answer traceable to the system's OWN evidence — a grounded answer can still be wrong, and that separation is the measurement |
| answer | `sealqa` / `simpleqa`, **or the backend's own verifier** | question, gold, answer | yes, for the judge | is the final answer correct |

**On a verifier-graded backend the answer step needs no judge**, and that is what makes the schema
complete where it matters most. A harbor cell declares `ground_truth: None`, so every `needs_gold`
judge is skipped — but the task's verifier already grades the answer and banks it as `env_reward`,
which a formula reads like any other term. The two evidence graders need no gold precisely so the
other two thirds survive there; a gold-comparing `evidence_retrieval` would have been dead on the
only backend whose cells are turn-structured enough to have steps at all.

Asking for SUFFICIENCY rather than correctness is also the better instrument, not just the
reachable one: handing a grader the gold invites it to accept any trace that merely *contains* the
gold string, and keeps the answer out of what is supposed to be measuring the search.

Two things the first two judges get right that are easy to get wrong. **A cell with no
`reasoning_trace` is ABSENT, never zero, and costs no model call** — a backend that emits no trace
has not produced a badly-grounded answer, and scoring it `UNGROUNDED` would report "the system
never uses evidence" for a run that simply routed through a backend with no trace channel. And
**the middle score is a prior we invented**: `PARTIAL = 0.5` is the same class of hand-set
threshold `verdict-resolution.md` § Phase 3 warns about, which is survivable only because
`_compute` banks the LABEL beside the score, so a later fit re-derives its own thresholds from
archived rows.

**Their rubrics are OURS, and that is the difference from `simpleqa.py`.** Nothing published grades
these two steps, so screen them (`seed-screen`, `noise-floor`) before funding a campaign on them
and record the reading in the dataset's `dataset.md`, exactly as any other instrument decision is
recorded.

## Identity — a judge that changed must re-cut the key

`Judge.fingerprint(spec)` hashes the judge name, its declared `version`, **its rubric text**, and
the whole stage chain. `pipeline_resolve.py::_identity_contributions` folds the whole term → judge
mapping into ONE `node_configs` entry under `JUDGE_INSTRUMENT_KEY`, so `sp_hash` moves — an archive
row is keyed on config, and a judge swapped silently would have every prior verdict replayed under
the new grader. **The TERM is inside that hash**: re-keying a grader banks a different set of
observations, so it is a different measurement even when the rubric and the models are identical.
Declaration order is not, because two campaigns declaring the same graders in a different order
measured the same thing.

**Hashing the rubric is why this is stronger than every published judge abstraction.** MLflow
versions server-side; pydantic-evals offers a hand-maintained `get_evaluator_version()` defaulting
to `None`. Nobody hashes the prompt, and nobody pins a model to more than a mutable alias. Here an
author who edits a rubric and forgets to bump `version` still moves the fingerprint.

## Emit absence, never zero

`compute` returns `float | None`, and `None` is **not** a zero: the materializer omits the key,
`cell_namespace` leaves the term unbound, and the formula raises `ScoringTermMissingError`. That
is the difference between *this answer was wrong* and *we did not find out*, and it is why
`call.py::ask` never raises — a provider hiccup must not be bankable as a wrong answer, nor kill
the measurement of a cell the backend already paid for.

The shipped SimpleQA judge **deliberately diverges from upstream here**: `simple-evals` defaults an
unparseable grading reply to `"C"` / `NOT_ATTEMPTED`, a category that does not count against
accuracy-given-attempted. Ours returns `None`. Record any such divergence in the dataset's
`dataset.md`.

## Why this package hand-rolls its own protocol

There is **no** cross-library standard for a judge OBJECT — ten libraries, ten abstractions. There
IS one for its OUTPUT, OpenTelemetry's `gen_ai.evaluation.result`, which `JudgeVerdict`'s field
names follow; adopting names costs nothing. Depending on a vendor's object does not work here:
ADR-0006 makes core the engine, a judge is reachable from the core measurement loop, so anything
it depended on would be a **core** dependency. A vendor adapter belongs in an entry-point plugin.

**A label is not a score**, which is why `to_score` is declared and `_validate` refuses a judge
whose labels and scores disagree either way — picking a numeric reading for a three-way taxonomy
silently is how `NOT_ATTEMPTED` becomes a zero that flatters an evasive model. Per-field detail
(including why `provenance` exists before any human rating does) lives on `protocol.py`'s own
field docs, not here.

## Registering one

**Identical to registering a connector, deliberately** — `_BUILTIN` data row, the
`promptpotter.judges` entry-point group, one `_validate` over both, no plugin shadowing a
built-in, a broken plugin fatal, `JUDGE_ORIGINS` as the audit surface. The reasoning for every
one of those, and the trusted-code boundary that comes with them, is owned by
[`../connectors/CLAUDE.md`](../connectors/CLAUDE.md) §§ Registering a connector · A connector is
trusted code. Read it there; nothing about a judge changes it.

## What is cached is the REPLY, not the verdict

`call.py::ask` reads and writes `Stores.judge_reuse`, keyed by `hash_call` over the rendered
prompt plus the stage's model / provider / temperature / max_tokens. **The stored artifact is the
model's reply**, and that choice is what makes ONE cache enough:

- **A rubric or model edit moves the key by itself** — the rendered prompt carries the rubric, the
  question, the gold and the prediction. So no judge-version component is needed here, and a judge
  whose `_parse` or `to_score` changed re-derives correctly from the stored reply: it is still what
  that model said. (Identity is a different question, answered by `fingerprint` above.)
- **A composition caches whole.** A second stage's prompt is a deterministic function of the
  first's reply — which is what `JudgeStage.temperature`'s `0.0` default buys — so a chain hits end
  to end under one key space with one invalidation.
- **The economically large hit is two candidates whose mutation did not change the answer**, which
  is the common case and is composition-independent.

Three rules ride with it, all inherited from `dispatch/llm_call/call.py` and each a scar: **meter
first, then store** (a disk error above the emit loses a row the provider already billed);
**meter cache hits too**, flagged, so grading cost stays invariant to our cache history; and
**never store an empty reply** — emptiness is transient, the key is the prompt hash, and the tree
is tenant-global, so caching one makes that comparison ungradeable forever with nothing on any
surface pointing at the cause.

A fourth rule is this seam's own: **absent, unreadable and stale are ONE answer — sample it again.**
Both the read and the validate raise, and `ask` sits under `measure_sample`'s catch-all, so a raise
here banks the whole cell as an ERROR and discards a backend answer already paid for. A cache
exists to make grading cheaper; nothing in it may ever cost a measurement.

**The handle reaches `ask` through a ContextVar, never through `GradeFn`.** `build_evaluators` takes
the cache, `_compute` scopes it with `call.py::bind_cache`, and `ask` reads it. Threading it
through `grade` would put a store handle in the judge protocol, so every judge author — ours and a
third party's — would carry infrastructure they have nothing to do with. **One cache across every
term**, because the key is the rendered prompt: two graders cannot read each other's replies, and
a step schema's three gradings of one comparison are each bought once.

`ask` also heartbeats and retries a 429, for the same reason the cache exists: a failed grade omits
the term, which halts the formula on that cell, which discards a backend answer already paid for.
One unretried rate limit throws away the expensive half of a measurement to save the cheap half.
