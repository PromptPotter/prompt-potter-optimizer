"""Campaign + cycle file I/O under ``campaigns/{campaign_id}/`` — one cohesive store.

``CampaignStore`` owns the whole ``campaigns/`` tree: the ``campaign.json`` manifest,
per-cycle ``index.json`` CRUD, fork-sibling index writers, round + candidate detail
files, and the cycle-seed ledger I/O (``CycleSeedRecord``).

**The read-once cycle seed rides the ledger** (declared-at-mint, read-once-at-init,
recovered by replay), NOT a ``.overrides/`` sidecar. Contrast ``.runtime/``
(``pause.flag`` / ``spend_cap.json``) — mutated-during-run, polled-every-tick, transient.
Don't conflate a durable ledger fact with a transient flag. Full contract:
``store.py``'s module docstring.

Note ``campaign.json`` here is the minted **manifest** (a frozen :class:`Campaign`),
NOT the dataset **template** of the same name under ``datasets/{name}/`` — two
incompatible schemas, one filename. See ``application/datasets/authored.py``.
"""
