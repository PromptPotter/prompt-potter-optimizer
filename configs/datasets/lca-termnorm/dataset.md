# LCA TermNorm — Dataset Context

## Type

`backend` — requires a running TermNorm backend.

## Prerequisites

- TermNorm backend must be running: `curl -s http://127.0.0.1:8000/status`
- If backend is down, tell the user: "Start the TermNorm backend first, then re-run `/potter-run`"

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id local
--config configs/datasets/lca-termnorm/campaign.json
```

`dataset_name` is set in `campaign.json` (`"train"` — 984 items). The `--dataset-name` CLI flag is optional; if provided it overrides the config value.

## Data

The `train` dataset is a mixed BOM (Bill of Materials) set combining:
- **Materials** (159 test items) — raw material names from engineering BOMs
- **Processing** (82 test items) — manufacturing process descriptions
- **Total train set**: 984 items (materials + processing combined with train split)

This matches the notebook's `prepare_datasets()` behavior which loads both BOM sheets from Excel.

Separate test splits are also available: `test_material` (165 items), `test_processes` (82 items).

## Pipeline Notes

- `llm_ranking` must always be excluded — it's broken (json_validate_failed on ~50% of queries)
- Effective pipeline: `cache_lookup -> fuzzy_matching -> web_search -> entity_profiling -> token_matching`
- Without `llm_ranking`, prompt string fields have no effect — optimization focuses on pipeline params
