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
