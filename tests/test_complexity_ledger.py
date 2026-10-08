"""Ratchet: the package's conceptual surface never moves unexamined, in either direction.

The rules are ``docs/developer/reasoning-doctrine.md`` ``<surface-ledger>``; a
move's reason goes in the COMMIT BODY, and ``git log -p`` is the history layer. This file is
only where the surface stands now — never a target to reach.
"""

from promptpotter.complexity_ledger import compute_ledger

LEDGER_BASELINE = {
    # Every `.py` under the package.
    "modules": 399,
    # Every `__init__.py` among them.
    "init_files": 55,
    # The `__init__.py` files that re-export names instead of staying empty.
    "reexport_shims": 7,
    # Campaign-config knobs, plus the node knobs each optimizer member declares.
    "config_leaf_fields": 72,
    # `Settings` fields — what the environment can set.
    "settings_env": 36,
    # The upper-case constants `config/settings.py` exports.
    "settings_const": 12,
    # Leaves of `OptSearchPoint` — what a run is handed.
    "opt_search_point_fields": 13,
    # Leaves of `CycleResult` — what a run produces.
    "cycle_result_fields": 198,
    # Parameters annotated `Any`.
    "any_params": 43,
    # `dict[str, Any]` maps declared in `domain/`.
    "domain_any_maps": 89,
    # Models that opt out of `StrictModel`'s `extra="forbid"`.
    "models_lax": 3,
    # The prompt decomposition fields.
    "prompt_string_fields": 6,
    # Injections the optimizers' runtimes price — each a block an optimizer prompt can carry.
    "injections": 34,
    # Escalation rules, priced the same way.
    "escalation_rules": 7,
    # Function-local imports of the package's own modules.
    "deferred_imports": 10,
    # `CLAUDE.md` files under the package.
    "claude_md": 10,
    # `tests/test_*.py` — `tests/CLAUDE.md` names what each one owns.
    "test_files": 7,
    # Test functions across them, each admitted through the charter's three axes.
    "test_functions": 100,
    # Every property of every schema the generated contract offers the browser.
    "served_fields": 857,
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
