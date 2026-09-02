"""Ratchet: the package's conceptual surface never moves unexamined, in either direction.

The rules are ``docs/developer/conventions.md`` § Reasoning doctrine ``<surface-ledger>``; a
move's reason goes in the COMMIT BODY, and ``git log -p`` is the history layer. This file is
only where the surface stands now — never a target to reach.
"""

from promptpotter.complexity_ledger import compute_ledger

LEDGER_BASELINE = {
    # +1: `domain/command_kinds.py` — the `/commands/{kind}` vocabulary, moved out of the
    # dispatcher that applies it. Not a new concept: the four parties that must agree on it
    # (dispatcher, router, CLI, TS codegen) could not all import the dispatcher, and the one that
    # could not is the CLI — which is why nothing bound the terminal to the command set and five
    # kinds shipped browser-only. The module is what makes `CLI_VERB_FOR_KIND` expressible.
    "modules": 331,
    "init_files": 48,
    "reexport_shims": 5,
    "config_leaf_fields": 39,
    "settings_env": 31,
    "settings_const": 14,
    "opt_search_point_fields": 39,
    # +1: `theta_caveat` on `ScoredCandidate` and `ScoreboardRow` — the per-ARM half of
    # `ThetaCaveat`, so a floor-pinned arm's θ is disclaimed on the row it invalidates rather
    # than only on the round's scale reading. A served state, not a derived one: the rows a
    # client would test are the per-sample arrays the candidate row exists to avoid shipping.
    # +1: `sp_hash` on `ScoredCandidate` — the searchpoint id, so a candidate names the archive
    # rows it paid for. Cannot be derived from what the model already carries: the sibling
    # `resolved_pipeline_params` has the rendered prompt stripped, and the hash covers it.
    # +1: `parent_results` on `RoundResult` — the bar the round's arms were measured against, on
    # the round's own subset. Not derivable from anything banked: round N-1's winner is the same
    # SEARCHPOINT but was read on cells this round never bought, and reconstructing it that way
    # is what left a sample-set mask re-scoring the arms and not the bar.
    "cycle_result_fields": 161,
    "any_params": 50,
    # +1: `results.py::is_floor_pinned(rows: Sequence[Mapping[str, Any]])`, the same signature as
    # `measured_cells` and `is_answer_collapsed` beside it — a round row read off disk is a plain
    # mapping, so a narrower annotation here would be a claim the callers cannot honour.
    # +4: `projection_envelope.py::ray_payload` and its `_pick` helper — the ray's field
    # projection. Both ends are genuinely untyped: the input is a `CycleRecord.model_dump()`
    # whose bulk sits under `payload: dict[str, Any]` on the models themselves, and the output is
    # `RayItem.payload`, the same shape narrowed. A model for the projection would have to
    # declare every kind's picked subset as a class, which is the hand-authored roster the
    # declaration exists to avoid.
    "domain_any_maps": 88,
    "models_lax": 3,
    "prompt_string_fields": 6,
    "injections": 32,
    "escalation_rules": 6,
    "claude_md": 7,
}


def test_complexity_ledger_ratchet() -> None:
    ledger = compute_ledger()
    assert set(ledger) == set(LEDGER_BASELINE), (
        "complexity-ledger dimensions changed; update LEDGER_BASELINE in this commit"
    )
    risen = {k: (v, LEDGER_BASELINE[k]) for k, v in ledger.items() if v > LEDGER_BASELINE[k]}
    assert not risen, (
        "conceptual surface grew (dimension: actual vs baseline) — a simplification "
        f"pass must lower the ledger, not raise it: {risen}. If this is a justified "
        "feature, raise the baseline deliberately; otherwise subtract instead of add."
    )
    fallen = {k: (v, LEDGER_BASELINE[k]) for k, v in ledger.items() if v < LEDGER_BASELINE[k]}
    assert not fallen, (
        "conceptual surface SHRANK while the baseline still reads the old number "
        f"(dimension: actual vs baseline): {fallen}. Lower it in this commit — a win "
        "nobody re-pins becomes silent headroom for the next raise, and the pass that "
        "earned it keeps no number to show for it."
    )
