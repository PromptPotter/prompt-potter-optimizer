# judges/ — LLM-as-judge graders for scoring

A judge grades one cell by asking a model where no deterministic matcher can. Adding one is local to `judges/<name>.py` plus a `_BUILTIN` row.

## A judge is a MEASUREMENT, never a formula term

**A judge runs ONCE, at measure time, and banks its verdict into the row; the pure formula reads that number by name.**
The formula re-runs offline over archived rows (δ ruler, A/B replay, resume), so a judge inside it would re-bill one LLM call per archived row on every index warm.

## The seam — a judge IS an `Evaluator`

**`build_evaluators(specs)` returns ordinary `per_sample` `Evaluator`s, one per TERM** (`needs_labels=True` skips them on a verifier-graded bank); values land top-level in `pipeline_data`. `_validate_evaluator` refuses an awaitable at `per_round`.
**Every judge evaluator goes through `validate_campaign_evaluator` — that call is the contract**: it refuses a non-identifier term, one `cell_namespace` binds itself, and one a package evaluator owns.

## Scoring, never the optimizer loop — and it is structural

**Nothing that sets the loop's LLMs reaches a judge** — models come only from `campaign.judges.{term}.stages`; required fields, exclusion from `param_keys` and the `judge` spend kind enforce it.
**Every shipped judge asks ONE stage** (`Judge.max_stages`, enforced by `build_evaluators`); a composing judge raises its own.

**A grader that FAILED is never bankable as a graded answer** — every declining arm returns `call.py::absent`, and verdicts are shaped through `call.py::graded`.
A judge reads its cell through `judge_question` / `judge_answer`; `predicted` read raw grades the `NO_RESULT` sentinel.

## The step schema — one per task SHAPE

**`campaign_config.judges` is keyed by the formula's TERM, never the judge's name** — that makes a multi-step cell expressible.
**A schema belongs to a task shape**: `retrieve → ground → answer` is the search-augmented one, `open → adhere` the agent-harness one (`spreadsheetbench/s10` tenant `README.md` § Step schema). Adopt a shipped schema where the shape matches; a third shape earns a third schema.
**A step may be graded by a connector observation** (`open` by `connectors/harbor.py::_skill_opened`, free) — look for the mechanical half before writing a rubric.
**The schema is fixed BEFORE a cell is bought** — the fingerprint folds the term → judge mapping, so re-keying is a new measurement; declaration order is step order.
Steps: retrieve = `evidence_retrieval` (no gold; never reads the answer); ground = `answer_grounding` (no gold; answer traceable to the cell's own evidence); answer = `sealqa` / `simpleqa` or the backend's verifier.
**On a verifier-graded backend the answer step needs no judge** — `env_reward` is the answer term.
**"What the cell DID" is `pipeline_data::turns` where present, else the `reasoning_trace` digest** (`grounding.py::_trace`), never a config key; turns render labelled.
**A missing input is absent and costs no call** — [`conventions.md`](../../docs/developer/conventions.md) § Code shape.
**Our rubrics (and `PARTIAL = 0.5`) are invented: screen them (`seed-screen`, `noise-floor`) before funding a campaign, and record the reading in the dataset's `README.md`.**

## Identity — a judge that changed must re-cut the key

**`Judge.fingerprint` hashes the rubric text — never trust a hand-bumped version**; `_identity_contributions` folds term → judge into `sp_hash`.
**The scorer id folds the same digest** (`judge_instrument` → `auto_scorer_id`), so one formula over two graders is two scorers.

## Emit absence, never zero

**`compute` returns `float | None`; `call.py::ask` never raises, and `registry.py::_compute` catches any grader failure and returns `absent`** — no grader failure costs the measurement it grades. A refused send (`CellSendRefusedError`) is the one exception.
**The SimpleQA judge diverges from upstream** (an unparseable reply is `None`, not `NOT_ATTEMPTED`); record any such divergence in the dataset's `README.md`.

## Registering one

**A connector's rules through the one loader** (`shared/plugin_registry.py`, the `promptpotter.judges` group, `judge_origins()` as audit surface) — owned by [`../connectors/CLAUDE.md`](../connectors/CLAUDE.md) §§ Registering a connector · A connector is trusted code.

## What is cached is the REPLY, not the verdict

**`call.py::ask` caches the model's REPLY**, keyed by `hash_call` over rendered prompt + model / provider / temperature / max_tokens, so a changed `_parse` or `to_score` re-derives. A hit is metered (`cached`); an empty reply is never stored.
