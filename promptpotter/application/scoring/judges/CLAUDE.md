# judges/ — LLM-as-judge graders for scoring

A judge grades one cell by asking a model where no deterministic matcher can. Adding one is local to `judges/<name>.py` plus a `_BUILTIN` row.

## A judge is a MEASUREMENT, never a formula term

**A judge runs ONCE, at measure time, and banks its verdict into the row; the pure formula reads that number by name** — the formula re-runs offline over archived rows, so a judge inside it would re-bill every archived row.

## The seam — a judge IS an `Evaluator`

**`build_evaluators(specs)` returns ordinary `per_sample` `Evaluator`s, one per TERM** (`needs_labels=True` skips them on a verifier-graded bank); values land top-level in `pipeline_data`.
**Every judge evaluator goes through `validate_campaign_evaluator` — that call is the contract**: it refuses a non-identifier term, one `cell_namespace` binds itself, and one a package evaluator owns.

## Scoring, never the optimizer loop

**Models come only from `campaign.judges.{term}.stages`** — required fields, exclusion from `param_keys` and the `judge` spend kind keep the loop's LLM settings away; `build_evaluators` refuses past `Judge.max_stages`.
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
**A missing input is absent and costs no call** — [`conventions.md`](../../../../docs/developer/conventions.md) § Code shape.
**Our rubrics (and `PARTIAL = 0.5`) are invented: screen them (`seed-screen`, `noise-floor`) before funding a campaign, and record the reading in the dataset's `README.md`.**

## Identity — a judge that changed must re-cut the key

**`Judge.fingerprint` hashes the rubric text — never trust a hand-bumped version**; `_identity_contributions` folds term → judge into `sp_hash`.
**The scorer id folds the same digest** (`judge_instrument` → `auto_scorer_id`), so one formula over two graders is two scorers.

## Emit absence, never zero

**`compute` returns `float | None`; `call.py::ask` returns a provider's failure (transport, status, malformed reply) as `absent`** — it costs the measurement it grades nothing. Any other exception, and a refused send (`CellSendRefusedError`), raises: the cell is an error row, never a no-grade.
**The SimpleQA judge diverges from upstream** (an unparseable reply is `None`, not `NOT_ATTEMPTED`); record any such divergence in the dataset's `README.md`.
