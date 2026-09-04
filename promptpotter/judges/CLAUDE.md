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

No new concept reaches the scoring layer. `build_evaluator(spec)` returns an ordinary
`application/scoring/evaluators.py::Evaluator` at `per_sample` scope, and four of its existing
fields already do the work:

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

## Scoring, never the optimizer loop — and it is structural

**Nothing that sets the loop's LLMs may reach a judge.** `allowed_models`,
`nodes.{node}.config.model`, the optimizer's own `assets/optimizer/pipeline.yaml`, and any global
"set every model" steer are all not consulted. A judge's models are declared in
`campaign.yaml::campaign_config.judge.stages` and inherited from nowhere; absent means absent, and
never a borrow.

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

## Identity — a judge that changed must re-cut the key

`Judge.fingerprint(spec)` hashes the judge name, its declared `version`, **its rubric text**, and
the whole stage chain. It rides
`pipeline_resolve.py::_identity_contributions` into `node_configs` under
`JUDGE_INSTRUMENT_KEY`, so `sp_hash` moves — an archive row is keyed on config, and a judge
swapped silently would have every prior verdict replayed under the new grader.

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

**The handle reaches `ask` through a ContextVar, never through `GradeFn`.** `build_evaluator` takes
the cache, `_compute` scopes it with `call.py::bind_cache`, and `ask` reads it. Threading it
through `grade` would put a store handle in the judge protocol, so every judge author — ours and a
third party's — would carry infrastructure they have nothing to do with.

`ask` also heartbeats and retries a 429, for the same reason the cache exists: a failed grade omits
the term, which halts the formula on that cell, which discards a backend answer already paid for.
One unretried rate limit throws away the expensive half of a measurement to save the cheap half.
