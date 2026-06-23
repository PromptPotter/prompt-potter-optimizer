"""Ratchet: the package's conceptual surface may shrink, never grow.

This is the enforcement teeth behind CLAUDE.md's <surface-ledger> rules. The
recurring AI blind spot is additive-but-safe "refactors" that grow the module +
import surface while claiming to simplify; a prose rule gets ignored, a red test
does not. Each dimension is pinned to a baseline and asserted ``<=`` — a change
that raises any dimension fails here and must justify itself as a feature, not a
refactor.

When a deletion legitimately LOWERS a dimension, lower its baseline in the same
commit (CLAUDE.md rule 4: "lower the baseline to lock the win"). The baseline IS
the finish line — when no number can fall further without losing a load-bearing
concept, the unification phase is done.
"""

from promptpotter.diagnostics.complexity_ledger import compute_ledger

# Captured 2026-06-19 after the unification-phase subtractions. Only ever edit a
# number DOWNWARD (a deletion that earned it) — never up without a feature reason.
# Deliberate feature raises since the 2026-06-19 capture: ``settings_const``
# 15→16 (the consent-gate security feature) and ``modules`` 293→295 (the durable
# check-in: ``infrastructure/store/checkin_draft_store.py`` + the two-transition
# seam ``application/jobs/launcher/checkin.py`` — a real disk-backed campaign
# authoring state that replaces the restart-wiped in-memory ``DraftCampaignRegistry``).
# Subtraction since: ``modules`` 295→294 (inlined the single-use
# ``validators/_text.py`` word-set helper into its lone consumer ``l2_output.py``);
# then ``modules`` 294→293 / ``init_files`` 55→54 / ``reexport_shims`` 42→41 (the
# de-obfuscation pass collapsed the single-module ``escalation/firing/`` package
# into one ``escalation/firing.py`` module, dropping its re-export ``__init__``);
# then ``modules`` 293→292 (inlined the single-consumer ``optimization/transitions.py``
# — ``TransitionResult`` + ``LayerStrategy`` — into its lone reader ``escalation/firing.py``);
# then ``settings_env`` 24→17 (collapsed the 8 per-provider ``*_RPM``/``*_TPM`` fields
# into one ``RATE_LIMITS`` provider→[rpm,tpm] map — the BYO/coupon prerequisite);
# then ``modules`` 292→293 (the storage-hover feature
# ``presentation/api/routers/campaigns/storage.py`` — the per-campaign on-disk
# size endpoint behind the sidebar hover card).
LEDGER_BASELINE = {
    "modules": 293,
    "init_files": 54,
    "reexport_shims": 41,
    "config_leaf_fields": 32,
    "settings_env": 17,
    "settings_const": 16,
    "opt_search_point_fields": 27,
    "prompt_string_fields": 6,
    "injections": 22,
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
