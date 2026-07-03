"""Ratchet: the package's conceptual surface may shrink, never grow.

This is the enforcement teeth behind the <surface-ledger> doctrine
(docs/developer/conventions.md § Reasoning doctrine). The
recurring AI blind spot is additive-but-safe "refactors" that grow the module +
import surface while claiming to simplify; a prose rule gets ignored, a red test
does not. Each dimension is pinned to a baseline and asserted ``<=`` — a change
that raises any dimension fails here and must justify itself as a feature, not a
refactor.

When a deletion legitimately LOWERS a dimension, lower its baseline in the same
commit (surface-ledger rule 4: "lower the baseline to lock the win"). The baseline IS
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
# size endpoint behind the sidebar hover card);
# then ``modules`` 293→294 (the measurement provenance grade
# ``domain/measurement_provenance.py`` — the deliberate-vs-incidental quality grade
# that de-biases the AxisIndex digest + gates clean-substrate reuse, the foundation
# the loop-improvement experiment and a future L4 ingest build on);
# then ``modules`` 294→293 (PoBB mid-round elimination moved to difficulty-adjusted
# θ ability — the same metric the round-winner election ranks by — which retired the
# Monte-Carlo ``paired_better_probabilities`` + the ``pobb/seeding.py`` MC-seed module,
# both replaced by the closed-form, deterministic ``metrics.py::elimination_p_best``).
# ``config_leaf_fields`` 32→33: a deliberate new operator knob —
# ``CampaignConfig.headline_metric`` (which fitness number headlines the text
# surfaces: accuracy/composite/θ). DISPLAY config, not a behavior knob; the gate
# stays θ. A feature, justified, so the baseline rises (per the surface-ledger rule).
# then ``modules`` 293→295 (the config coupling/provenance map — the SoT
# ``application/config_coupling.py`` declaring which knob moves which statistical
# estimand + which knobs collide, and its CLI ``diagnostics/config_map.py``. Lifts
# the "deferred-with-the-flip" knob interactions out of spec prose into one
# machine-checked registry, read by the preflight gate, the diagnostic, and the
# webapp config-map panel. A feature — operator-requested collision visibility).
# then ``modules`` 295→293 (deleted the dead cross-cycle compare subsystem — the
# unused ``compare`` CLI verb + ``pobb/elevation.py`` (elevate_to_decisive /
# discover_compare_arms / the persisted δ-bank) + ``posterior_best_from_normals``.
# Reachable only via the never-used ``compare`` verb — no live-loop caller — and
# superseded by the deterministic A/B replay engine).
# then ``modules`` 293→295 (the deterministic A/B replay engine that replaces it:
# ``resume_and_fork/ab_replay.py`` re-derives a recorded cycle's decisions under the
# current engine/scorer over the recorded measurements + the ``ab`` CLI verb. A feature
# — the real, deterministic engine/scorer A/B; net-neutral on module count vs the deleted
# subsystem but −640 lines and a sharper concept).
# then ``modules`` 295->296: the in-process ``llm_only`` connector
# (`connectors/llm_only.py`) — a single direct LLM call on the shared `in_process`
# execution seam, so the basic case runs with no TermNorm server (l4-outer-loop
# § Feature A). A feature, justified, so the baseline rises.
# then ``config_leaf_fields`` 33->34: a deliberate new operator knob --
# ``ExplorationConfig.enable_2pl_graduation`` (let the difficulty ruler graduate
# 1PL->2PL where a data-rich dataset wins held-out CV; fitness-comparability slice 3).
# A feature, justified, so the baseline rises (per the surface-ledger rule).
# then ``modules`` 296->297: the L4 inner-cycle runner
# (`application/runner/inner_recursion.py`) — mints + runs a sandboxed inner
# PromptPotter campaign in its own asyncio task under the spawning cycle's
# `.runtime/inner/`, returning the three proxy metrics (l4-outer-loop slice 2,
# the actual L4 recursion). A feature, justified, so the baseline rises.
# then ``config_leaf_fields`` 34->35: a deliberate new operator knob --
# ``OptimizationConfig.optimizer_set`` (selects the optimizer meta-prompt set per
# cycle: default ``_optimizer/`` vs the L4 outer ``_optimizer_meta/`` whose L1
# emits per-node inner-meta-prompt edits; l4-outer-loop slice 3b, the gating
# slice). A feature, justified, so the baseline rises.
# then ``config_leaf_fields`` 35->36: a deliberate new operator knob --
# ``mechanisms.elimination.equivalence_elimination`` (the practical-equivalence /
# futility gate — cut a candidate once it's improbable to clear the round's
# adoption bar seed+improvement_threshold, so a tie doesn't ride the full panel;
# the probabilistic sibling of deterministic_dominance). A feature, justified.
# injections 22→23: ``sample_transcripts`` — complete failing samples (full query
# + the model's reasoning trace) on the distiller's floor. The prior surface
# showed no LLM tier one complete failure (2 mid-word-truncated stems), which
# starved the critique into unverifiable steers. A feature, justified.
# then ``config_leaf_fields`` 36->37: a deliberate new operator knob --
# ``OptimizationConfig.noop_probe`` (inject one origin-identical NO-OP arm in
# round 1; its measured delta vs origin is the backend's run-to-run noise floor —
# the yardstick real candidate deltas must clear on a stochastic backend, the L4
# inner recursion being the canonical user). A feature, justified.
# then ``modules`` 297->298: extracted the ONE in-flight heartbeat loop out of
# ``dispatch/llm_call/call.py`` into ``dispatch/llm_call/heartbeat.py`` so the L4
# outer cycle can ride the same loop (with a live inner-progress ``detail_fn``)
# while it awaits a multi-minute inner campaign — instead of a second, duplicate
# heartbeat. A shared-mechanism extraction (two callers now, no redundant loop),
# justified, so the baseline rises.
# then ``modules`` 298->299: extracted ``cmd_verify``'s application-layer body
# (OSP rebuild / scoring / composite-fitness / DiagnosticRunRecord assembly) out
# of the CLI command into ``application/verify.py::verify_candidate`` —
# `presentation/CLAUDE.md` bans business logic in CLI commands. A layering fix,
# not a ledger-down subtraction (code-debt-cleanup.md Ready-bucket item), so the
# baseline rises rather than falls.
LEDGER_BASELINE = {
    "modules": 299,
    "init_files": 54,
    "reexport_shims": 41,
    "config_leaf_fields": 37,
    "settings_env": 17,
    "settings_const": 16,
    "opt_search_point_fields": 27,
    "prompt_string_fields": 6,
    "injections": 23,
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
