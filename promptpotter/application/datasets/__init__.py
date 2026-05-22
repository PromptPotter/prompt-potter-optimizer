"""Dataset loaders, per-dataset prompt store, and the potter-trace loader.

Three concerns, one per submodule:

* ``loaders`` — ground-truth loaders (Excel, HuggingFace) + the
  train/test splitter + the ``DATASET_LOADERS`` registry + the
  measurement-batch builder ``build_dataset_run_data``.
* ``prompts`` — the per-dataset starting-point prompt store
  (``datasets/{name}/prompts/`` resolution) + the backend node overlay.
* ``traces`` — the potter-trace dataset loader (one row per
  round-to-round transition across archived campaigns; raw material for
  self-optimization).
"""

from promptpotter.application.datasets.loaders import (
    DATASET_LOADERS,
    SHEET_COLUMN_MAP,
    build_dataset_run_data,
    load_aime_2025,
    load_bbeh,
    load_dataset,
    load_excel_ground_truth,
    load_gsm8k,
    load_justlogic,
    sample_dataset,
    samples_from_dicts,
    split_train_test,
)
from promptpotter.application.datasets.prompts import (
    dataset_prompt_dir,
    has_dataset_prompts,
    list_dataset_prompts,
    load_dataset_node_overlay,
    load_dataset_prompt,
    load_node_prompt,
)
from promptpotter.application.datasets.traces import load_potter_traces

__all__ = [
    "DATASET_LOADERS",
    "SHEET_COLUMN_MAP",
    "build_dataset_run_data",
    "dataset_prompt_dir",
    "has_dataset_prompts",
    "list_dataset_prompts",
    "load_aime_2025",
    "load_bbeh",
    "load_dataset",
    "load_dataset_node_overlay",
    "load_dataset_prompt",
    "load_excel_ground_truth",
    "load_gsm8k",
    "load_justlogic",
    "load_node_prompt",
    "load_potter_traces",
    "sample_dataset",
    "samples_from_dicts",
    "split_train_test",
]
