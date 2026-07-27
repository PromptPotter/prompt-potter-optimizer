"""Store — focused leaf stores for file-based persistence.

Nothing is re-exported here, and that is load-bearing: an eager leaf import drags in
``CampaignStore``, which imports back up to ``runtime_flags`` and ``ledger`` — a cycle.
Import each leaf directly; the map is ``infrastructure/CLAUDE.md`` § Stores.
"""
