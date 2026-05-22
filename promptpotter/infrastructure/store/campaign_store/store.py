"""Campaign + cycle artifacts under ``campaigns/{campaign_id}/``.

A campaign is a **forest**: it holds N *session* roots (one per ``new`` on
the same origin declaration) plus their fork descendants, all flat under
``cycles/``. ``list_sessions`` enumerates the forest roots.

``CampaignStore`` composes its method surface from four mixins over a
shared kernel (``CampaignStoreKernel`` — path resolution + the
campaign/cycle reads used across mixins):

* **manifest** (``manifest.py``) — ``campaign.json`` manifest CRUD; the
  campaign owns the frozen ``CampaignConfig`` snapshot and the forest's
  identity (``campaign_id = {dataset}__{hash}``).
* **cycles** (``cycles.py``) — per-cycle ``index.json`` CRUD, rewind, and
  cross-campaign enumeration. Every cycle method takes a leading
  ``campaign_id`` because a cycle id is unique only within its campaign —
  the path spine is ``(campaign_id, cycle_id)``.
* **forks** (``forks.py``) — divergence / diag / sweep fork ``index.json``
  writers.
* **rounds** (``rounds.py``) — round + candidate detail-file CRUD.
"""

from __future__ import annotations

from promptpotter.infrastructure.store.campaign_store.cycles import CycleIndexMixin
from promptpotter.infrastructure.store.campaign_store.forks import ForkMixin
from promptpotter.infrastructure.store.campaign_store.manifest import CampaignManifestMixin
from promptpotter.infrastructure.store.campaign_store.rounds import RoundMixin


class CampaignStore(CampaignManifestMixin, CycleIndexMixin, ForkMixin, RoundMixin):
    """File I/O for the ``campaigns/{campaign_id}/`` tree."""


__all__ = ["CampaignStore"]
