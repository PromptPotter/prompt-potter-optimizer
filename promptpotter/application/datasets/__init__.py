"""Dataset loaders and per-dataset prompt store.

Two concerns, one per submodule:

* ``loaders`` — ground-truth loaders (Excel, HuggingFace) + the
  train/test splitter + the ``DATASET_LOADERS`` registry + the
  measurement-batch builder ``build_dataset_run_data``.
* ``prompts`` — the per-dataset starting-point prompt store
  (``datasets/{name}/prompts/`` resolution) + the backend node overlay.
"""
