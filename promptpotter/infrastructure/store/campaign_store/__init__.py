"""Per-cycle optimization artifacts under ``campaigns/``.

The original ``campaign_store.py`` (~722 lines) is split into:

* :mod:`store` — the :class:`CampaignStore` class (the I/O surface).
* :mod:`ledger_scan` — pure ledger reader
  (:func:`scan_ledger_max_round_complete`) used by ``rewind_to_round``
  for admissibility checks. Pure file read, no subscribers fire.
* :mod:`index_helpers` — pure ``index.json`` shape helpers
  (:func:`round_summary`, :func:`fresh_sibling_index_blob`).

Existing imports continue to work; :class:`CampaignStore` is re-exported here.
"""

from __future__ import annotations

from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = ["CampaignStore"]
