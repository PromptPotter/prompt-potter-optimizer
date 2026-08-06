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
    # 16 -> 19 (2026-08-05): `BRAND_SHORT_NAME` / `BRAND_SERVICE_NAME` / `BRAND_DOCS_URL`.
    # A deliberate raise. They are the engine's half of the whitelabel declaration —
    # `deploy-linux/deploy.config`'s brand block, fanned out by `brand-env.sh` into `.env`
    # here and into `NEXT_PUBLIC_*` for the webapp build — and what they replace is five
    # hardcoded product names a distributor could not repaint at all (the CLI greeting, the
    # first-run key prompt, the argparse description, the FastAPI title, `/health`'s
    # `service`). Env is the right channel because the value is per-INSTALL, chosen by the
    # distributor at deploy time; a campaign-scoped knob would have been the wrong tier.
    "settings_env": 19,
    "settings_const": 15,
    "opt_search_point_fields": 25,
    "any_params": 65,
    # New (2026-08-05). Lowering it is what makes the cycle-index modelling question
    # (`docs/specs/code-debt-cleanup.md`) falsifiable rather than a judgment call.
    # 72 -> 73 (2026-08-06): `cell_readings`. A deliberate raise that buys a subtraction — it
    # is the SOLE definition of what counts as a measurement of an outer cell, with
    # `cell_fitness` a mean over it. The paired verdict and the noise series used to decide
    # that separately, and the noise series decided it wrong.
    "domain_any_maps": 73,
    # 4051 -> 4053 (2026-08-06): `_sample_series(sample_id, dots)` in the datasets router.
    # A deliberate raise, for two params that buy the deletion of a rule the webapp could
    # not keep: the hard-sample roster counted its own hit rate off the dots in hand, which
    # on a graded scorer (`is_hit` = `fitness >= 1.0`, unreachable there) printed 0/N on
    # every row of a healthy campaign. Serving the aggregate beside the series it aggregates
    # is what makes the two unable to disagree — a reader-side sum over a different set is
    # exactly the failure this replaces, so the surface moves to the side that owns it.
    # 4053 -> 4056 (2026-08-06): `cell_readings` + `_cell_reading_lists` + `cells_dropped`.
    # A deliberate raise. The first two split "every reading of a cell" from "its mean" so
    # replicate spread survives to the consumer whose job is measuring spread; `cells_dropped`
    # is the loud half of the error exclusion, since a census panel that quietly returns fewer
    # cells cannot be told from a small one.
    "param_decls": 4056,
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
