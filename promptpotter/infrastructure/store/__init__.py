"""Store — focused leaf stores for file-based persistence.

Nothing is re-exported here, and that is load-bearing: this file used to import all ten
leaf stores eagerly, so importing *any* leaf (``store.io``, ``store.layout`` — both pure,
neither able to cycle on its own) executed it and dragged in ``CampaignStore``, which
imports back up to ``runtime_flags`` and ``ledger``. Every entry point that reached
``ledger`` or ``runtime_flags`` before ``store`` got an ImportError, which is how CI's
``scripts/build_ts_types.py`` went red. Three back-edges were cut to work around it; with
no eager imports left, the cycle is structurally impossible rather than dodged.

CONCEPT MAP (import each leaf directly):
* **stores** (:mod:`.stores`) — :class:`Stores` frozen bundle + :func:`build_stores`
  (composite over the leaves); ``OptimizerCallCache`` (SHA-256-keyed, cross-fork
  optimizer-call cache, mirror of the archive).
* **leaf stores** — :class:`BackendStore` (:mod:`.backend_store`), :class:`CampaignStore`
  (:mod:`.campaign_store.store`), :class:`SessionStore` (:mod:`.session_store`),
  :class:`SweepStore`, :class:`DiagnosticRunStore`, :class:`TenantDatasetStore`,
  :class:`UserStore`, :class:`CheckinDraftStore`.
* **io** (:mod:`.io`) — shared I/O (``write_json`` / ``read_json`` /
  ``validate_path_component``). Pure; depends on nothing in the package.
* **layout** (:mod:`.layout`) — pure campaign/cycle dir builders + cycle-id parsing
  (``cycle_dir_for``, ``root_cycle_id``, ``sibling_kind``, …).
* **session_pointer** (:mod:`.session_pointer`) — the per-tenant active-session pointer
  (``mint_session_id`` / ``save_active_pointer`` / ``read_active_pointer`` / …). Lived in
  this file until it was the only reason the package needed a body.
* **dataset_access** — identity-aware dataset read gateway.
* **measurement_archive** — :class:`MeasurementArchive`, the append-only content-addressed
  DB core (sole source for the derived intelligence views).
* **archive_views** / **lineage_views** — derived reads as free functions over the archive.
"""
