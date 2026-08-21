"""Ratchet: the package's conceptual surface never moves unexamined, in either direction.

The rules are ``docs/developer/conventions.md`` § Reasoning doctrine ``<surface-ledger>``; a
move's reason goes in the COMMIT BODY, and ``git log -p`` is the history layer. This file is
only where the surface stands now — never a target to reach.
"""

from promptpotter.diagnostics import compute_ledger

LEDGER_BASELINE = {
    "modules": 327,
    "init_files": 48,
    "reexport_shims": 6,
    "config_leaf_fields": 39,
    "settings_env": 30,
    "settings_const": 15,
    "opt_search_point_fields": 25,
    "any_params": 50,
    "domain_any_maps": 82,
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
