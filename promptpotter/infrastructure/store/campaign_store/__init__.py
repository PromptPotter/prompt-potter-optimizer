"""Campaign + cycle file I/O under ``campaigns/{campaign_id}/`` — one cohesive store.

``CampaignStore`` owns the whole ``campaigns/`` tree: the ``campaign.json`` manifest,
per-cycle ``index.json`` CRUD, fork-sibling index writers, round + candidate detail
files, and the per-cycle ``.overrides/seed.json`` home.

**The directory name encodes read cadence.** ``.overrides/`` is declared-at-mint,
read-once-at-bootstrap (the cycle seed); ``.runtime/`` (``pause.flag`` /
``spend_cap.json``) is mutated-during-run, polled-every-tick. Conflating the two
invites cache-staleness bugs. Full contract: ``store.py``'s module docstring.

Note ``campaign.json`` here is the minted **manifest** (a frozen :class:`Campaign`),
NOT the dataset **template** of the same name under ``datasets/{name}/`` — two
incompatible schemas, one filename. See ``application/datasets/authored.py``.
"""

from __future__ import annotations

from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = ["CampaignStore"]
