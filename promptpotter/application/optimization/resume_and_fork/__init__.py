"""Resume + fork-on-divergence machinery — one module, one public surface.

When fork-on-divergence breaks, this is the one package to open, and three prohibitions
keep it that way: no replayer registered outside ``REPLAYERS``, no fork-mint path outside
``_mint_fork`` (a new trigger is a :class:`ForkTrigger` addition, never a parallel mint),
and replayers MUST NOT touch the live ``Cycle`` or write to the ledger — they re-derive,
they do not run.
"""
