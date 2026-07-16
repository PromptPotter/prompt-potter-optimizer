"""Typed View dataclasses + the PhaseEvent→View builder + markdown rendering.

This is the **application's emit contract**: ``run_observers`` builds typed
views from ``PhaseEvent``s (``from_phase_event`` needs ``optimizer_model`` +
the scoring formula evaluators, both same-layer), Pydantic serializes them
byte-identically to disk + SSE, and the disk-side writers render them to
``log.md`` / ``summary.md`` markdown. Because producing these views *is* an
orchestration job, they live in ``application/`` — ``presentation/`` imports
them UPWARD (the allowed direction).

Genuinely-terminal rendering (ANSI ``to_text`` / ``render_sp_diff`` / the
``live/`` ledger subscriber) stays in ``presentation/views`` — that's display.
"""
