"""Campaign + cycle file I/O under ``campaigns/{campaign_id}/``.

* :mod:`store` — the :class:`CampaignStore` class, composed from the four
  mixin modules over the shared :mod:`_kernel`.
* :mod:`_kernel` — path resolution + the campaign/cycle reads used across
  mixins.
* :mod:`manifest` / :mod:`cycles` / :mod:`forks` / :mod:`rounds` — the four
  method groups: ``campaign.json`` CRUD, cycle ``index.json`` CRUD, fork
  index writers, round + candidate file CRUD.
* :mod:`ledger_scan` — pure ledger reader
  (:func:`scan_ledger_max_round_complete`) used by ``rewind_to_round`` for
  admissibility checks. Pure file read, no subscribers fire.
* :mod:`index_helpers` — pure ``index.json`` shape helpers
  (:func:`round_summary`, :func:`fresh_sibling_index_blob`).
"""

from __future__ import annotations

from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = ["CampaignStore"]
