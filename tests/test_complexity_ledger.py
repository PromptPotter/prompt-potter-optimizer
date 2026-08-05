"""Ratchet: the package's conceptual surface never moves unexamined.

The enforcement teeth behind the ``<surface-ledger>`` doctrine
(``docs/developer/conventions.md`` § Reasoning doctrine). The recurring AI blind spot is
additive-but-safe "refactors" that grow the module + import surface while claiming to
simplify; a prose rule gets ignored, a red test does not. Each dimension is pinned to a
baseline and asserted for EQUALITY, so a move in EITHER direction stops here and asks for
a reason — neither is forbidden.

Both directions, because a one-way ratchet re-pins only on a raise: an unrecorded drop
becomes silent headroom for the next raise, and the baselines drift loose from the package
they claim to measure until nobody can read a number off them.

**Moving a baseline.** Edit the number in the same commit as the change, and put the
reason in the COMMIT BODY, not here: up = a feature or a shape that makes the codebase
quicker to develop, down = a deletion earned it (surface-ledger rule 4 — lower it to lock
the win, so it cannot drift back). What is banned is growing the surface while calling
itself a refactor. `git log -p tests/test_complexity_ledger.py` is the history of every
move; this file is only where the surface stands now, and it is not a target to reach.

The comments below record why a baseline sits where it does — the precedent
``<surface-ledger>`` rule 1 points at. What a dimension COUNTS, and which of its members
are a deliberate FLOOR rather than debt, is on the matching ``_count_*`` in
``promptpotter/diagnostics.py``; do not restate it here.
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
    "settings_const": 15,
    "opt_search_point_fields": 25,
    "any_params": 65,
    # New (2026-08-05). Lowering it is what makes the cycle-index modelling question
    # (`docs/specs/code-debt-cleanup.md`) falsifiable rather than a judgment call.
    "domain_any_maps": 72,
    # 4051 -> 4052 (2026-08-05): `_count_domain_any_maps`'s own parameter. Adding a counter
    # costs one declaration, which is the dimension counting itself and correct.
    "param_decls": 4052,
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
    fallen = {k: (v, LEDGER_BASELINE[k]) for k, v in ledger.items() if v < LEDGER_BASELINE[k]}
    assert not fallen, (
        "conceptual surface SHRANK while the baseline still reads the old number "
        f"(dimension: actual vs baseline): {fallen}. Lower it in this commit. A ratchet "
        "that only asserts one direction re-pins only on a RAISE, so every unrecorded "
        "win silently becomes headroom for the next one and the baselines rot loose — "
        "`settings_const` sat a full point above the truth for exactly that reason. It "
        "also robs the pass that earned the win of the number that proves it."
    )
