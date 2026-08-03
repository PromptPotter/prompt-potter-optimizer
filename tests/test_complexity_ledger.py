"""Ratchet: the package's conceptual surface never moves unexamined.

The enforcement teeth behind the ``<surface-ledger>`` doctrine
(``docs/developer/conventions.md`` § Reasoning doctrine). The recurring AI blind spot is
additive-but-safe "refactors" that grow the module + import surface while claiming to
simplify; a prose rule gets ignored, a red test does not. Each dimension is pinned to a
baseline and asserted ``<=``, so a raise stops here and asks for a reason — it is not
forbidden.

**Moving a baseline.** Edit the number in the same commit as the change, and put the
reason in the COMMIT BODY, not here: up = a feature or a shape that makes the codebase
quicker to develop, down = a deletion earned it (surface-ledger rule 4 — lower it to lock
the win, so it cannot drift back). What is banned is growing the surface while calling
itself a refactor. `git log -p tests/test_complexity_ledger.py` is the history of every
move; this file is only where the surface stands now, and it is not a target to reach.

The comments below are the ones a reader still needs: what a dimension counts when the
name does not say it, and which members are a deliberate FLOOR rather than debt.
"""

from promptpotter.diagnostics import compute_ledger

LEDGER_BASELINE = {
    # 298 -> 300 (2026-08-02): the `seed-screen` diagnostic — `application/seed_screen.py` +
    # its CLI shell. A deliberate raise, and the reason is that the choice it makes was
    # previously being made by nobody: the L4 panel's six seed indices were the first six
    # integers, and one of them (seed-5) drew a bank whose 40 rows contained 7 nothing could
    # solve and 4 everything could, leaving a 16-row instrument that carried ~2x the panel's
    # residual noise. Screening a bank BEFORE spending eleven minutes per cell on it is a
    # capability, not a relocation — and it is fenced exactly like `noise-floor`, so the loop
    # never learns it exists. The alternative was a scratchpad script, which would have left
    # the panel's composition unreproducible.
    "modules": 300,
    "init_files": 47,
    # A FLOOR, not debt. The 5 survivors are not shims and emptying them breaks the app:
    #   * ``connectors`` — IS the connector registry (import-time guards).
    #   * ``presentation/api/routers/campaigns`` — IS the route registry: its submodule
    #     imports run the ``@campaigns_router`` decorators, so emptying it mounts a router
    #     with ZERO routes. The one that reads like a pure re-export and is not.
    #   * ``shared``, ``application/scoring/formula``, ``application/views/render`` — real
    #     code in the body (the counter sees ``__all__`` + an import and says "shim").
    "reexport_shims": 5,
    # ``len(KNOBS)`` — the registry in ``application/knobs.py`` IS the taxonomy, so the
    # ledger does not carry a second opinion about what counts as one leaf.
    #
    # 38 -> 39 (2026-08-03): ``optimization.panel_gate``. A deliberate raise for a decision
    # nothing could previously express: whether a round may ELECT on a panel with holes —
    # cells the loop attempted and got no measurement back from. It is not a variant of the
    # two gates beside it (``origin_gate`` grades round 0's floor, the backend gate grades
    # reachability) and not a second under-probing guard (``coverage_floor`` decides who may
    # win and excludes; this decides whether the round closes at all and halts). Without the
    # knob the halt would be unconditional, and the operator who genuinely wants to elect on
    # a short panel would have no way to say so — which is the shape a gate needs to be a
    # policy rather than a law.
    "config_leaf_fields": 39,
    "settings_env": 16,
    "settings_const": 16,
    "opt_search_point_fields": 25,
    # A bare ``x: Any`` parameter whose real type exists and simply was not written — surface
    # a reader must carry in their head while the checker cannot see it. It earns a dimension
    # because ``strict = true`` STRUCTURALLY cannot catch it: ``disallow_untyped_defs`` is
    # satisfied (``Any`` IS an annotation) and ``warn_return_any`` is defeated by any
    # expression unioning ``Any`` with a concrete type. ``disallow_any_explicit`` is not the
    # fix — it rejects ``dict[str, Any]`` (honest, for raw JSON) exactly as hard.
    # Marches to a FLOOR, not to zero: ~30 are honest (raw JSON pre-parse, provider SDK
    # payloads behind ``follow_imports="skip"``), and ``**kwargs: Any`` is excluded outright.
    "any_params": 65,
    # A Pydantic model that does NOT end up ``extra="forbid"``, so an unknown key is dropped
    # instead of raised. Also a FLOOR — the 4 each name their reason on the model itself:
    #   * ``RoundResult`` / ``ScoredCandidate`` — ``@computed_field`` round-trip. Pydantic
    #     serializes a computed field OUT and rejects it back IN, so ``model_dump()`` writing
    #     the round file and ``model_validate()`` reading it back are only compatible while
    #     extras are ignored. Verified: forbid breaks every real round file on disk.
    #   * ``NodePromptInfo`` — the BACKEND owns that sub-object's vocabulary; a backend PP
    #     does not own must be free to describe itself without crashing PP.
    #   * ``Sample`` — a dataset row carries whatever columns the operator's file had.
    # ``ObservationMapping`` is deliberately NOT lax though it also parses ``pipeline.yaml``:
    # PP owns that vocabulary, so an unknown key there is a typo.
    "models_lax": 4,
    "prompt_string_fields": 6,
    "injections": 25,
    "escalation_rules": 6,
    "claude_md": 7,
}


def test_complexity_ledger_ratchet() -> None:
    ledger = compute_ledger()
    # Adding/removing a dimension must update the baseline in the same commit.
    assert set(ledger) == set(LEDGER_BASELINE), (
        "complexity-ledger dimensions changed; update LEDGER_BASELINE"
    )
    risen = {k: (v, LEDGER_BASELINE[k]) for k, v in ledger.items() if v > LEDGER_BASELINE[k]}
    assert not risen, (
        "conceptual surface grew (dimension: actual vs baseline) — a simplification "
        f"pass must lower the ledger, not raise it: {risen}. If this is a justified "
        "feature, raise the baseline deliberately; otherwise subtract instead of add."
    )
