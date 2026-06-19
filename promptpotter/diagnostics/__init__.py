"""Cross-cutting diagnostics that read the whole package surface.

Lives outside the layer tree on purpose: the complexity ledger imports from
`domain/`, `application/`, `config/`, and `optimization/` to count their
surface, so it cannot sit inside any single layer without inverting an import
direction. It is a read-only tool, never imported by the runtime loop.
"""
