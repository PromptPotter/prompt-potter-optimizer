"""LiveDashboardView — operator-facing ``dashboard.json`` writer.

Per-cycle and never aggregated: a fork seeds its prior trajectory from the parent's file
and writes only its own thereafter (``infrastructure/CLAUDE.md``). Split into submodules
so the pure shaping — :mod:`render`, :mod:`round_summary`, :mod:`state` — stays outside
the ledger-subscriber loop in :mod:`view`.
"""
