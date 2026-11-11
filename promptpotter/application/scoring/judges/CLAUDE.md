# judges/ — LLM-as-judge graders

Adding one is `judges/<name>.py` plus a `_BUILTIN` row (`registry.py`).

## A judge is a MEASUREMENT, never a formula term

**A judge runs ONCE, at measure time, and banks its verdict into the row; the pure formula reads that number by name**, so it re-runs offline over archived rows. One `per_sample` `Evaluator` per TERM, refused where another owner binds its name.

## The step schema — one per task SHAPE

- **`campaign_config.judges` is keyed by the formula's TERM, never the judge's name**, so a multi-step cell is expressible.
- **A schema belongs to a task shape**: `retrieve → ground → answer` is the search-augmented one, `open → adhere` the agent-harness one; a third shape earns a third schema. retrieve = `evidence_retrieval` (no gold; never reads the answer); ground = `answer_grounding` (traceable to the cell's own evidence); answer = `sealqa` / `simpleqa`, or the backend's verifier (`env_reward`). A step may be graded by a free connector observation (`open` by `connectors/harbor.py::_skill_opened`).
- **The schema is fixed BEFORE a cell is bought**: `Judge.fingerprint` hashes the rubric text into `sp_hash` and the scorer id, so one formula over two graders is two scorers.
- **Our rubrics (and `PARTIAL = 0.5`) are invented**: screen them (`seed-screen`, `noise-floor`) before funding a campaign; the reading goes in the dataset's `README.md`.

## Emit absence, never zero

`compute` returns `float | None`; a provider's failure is `absent`, costing the measurement nothing. Any other exception, and a refused send (`CellSendRefusedError`), raises: the cell is an error row, never a no-grade. A divergence from upstream (SimpleQA: an unparseable reply is `None`, not `NOT_ATTEMPTED`) goes in the dataset's `README.md`.
