# judges/ — LLM-as-judge graders

Adding one is `judges/<name>.py` plus a `_BUILTIN` row (`registry.py`); its `Evaluator` goes through `validate_campaign_evaluator`.

## A judge is a MEASUREMENT, never a formula term

**A judge runs ONCE, at measure time, and banks its verdict into the row; the pure formula reads that number by name** (it re-runs offline over archived rows). `build_evaluators(specs, beside=…)` returns `per_sample` `Evaluator`s, one per TERM, each refused where another owner binds its name (`evaluators.py::bound_by`, the campaign's connector report and observation keys beside the package's) (`needs_labels=True` skips them on a verifier-graded bank); values land top-level in `pipeline_data`.

## The step schema — one per task SHAPE

- **`campaign_config.judges` is keyed by the formula's TERM, never the judge's name**, so a multi-step cell is expressible.
- **A schema belongs to a task shape**: `retrieve → ground → answer` is the search-augmented one, `open → adhere` the agent-harness one (`spreadsheetbench/s10` tenant `README.md` § Step schema); a third shape earns a third schema. Steps: retrieve = `evidence_retrieval` (no gold; never reads the answer); ground = `answer_grounding` (answer traceable to the cell's own evidence); answer = `sealqa` / `simpleqa`, or the backend's verifier (`env_reward`, no judge).
- **A step may be graded by a connector observation** (`open` by `connectors/harbor.py::_skill_opened`, free) before a rubric.
- **The schema is fixed BEFORE a cell is bought**: `Judge.fingerprint` hashes the rubric text, `pipeline_resolve.py::identity_layer` folds term → judge into `sp_hash`, and the scorer id folds the same digest (`judge_instrument` → `auto_scorer_id`), so one formula over two graders is two scorers.
- **Our rubrics (and `PARTIAL = 0.5`) are invented**: screen them (`seed-screen`, `noise-floor`) before funding a campaign; the reading goes in the dataset's `README.md`.

## Emit absence, never zero

`compute` returns `float | None`; `call.py::ask` returns a provider's failure as `absent`, costing the measurement nothing, and every declining arm returns `absent`. Any other exception, and a refused send (`CellSendRefusedError`), raises: the cell is an error row, never a no-grade. The SimpleQA judge diverges from upstream (an unparseable reply is `None`, not `NOT_ATTEMPTED`); record any such divergence in the dataset's `README.md`.
