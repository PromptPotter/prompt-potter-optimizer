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
# (`application/runner/inner/cycle.py`) — mints + runs a sandboxed inner
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
# then ``modules`` 299->300: the liveness reaper
# (``application/jobs/reaper.py``) — the single owner reconciling on-disk cycle
# state with real producer liveness. Post in-flight-heartbeat a stale dashboard
# means a DEAD producer, so a persistently-detached cycle is stamped TERMINAL
# (``PRODUCER_VANISHED``) instead of haunting the OS-style dock forever. Two
# callers (JobRegistry ``on_reap`` + a startup sweep), one idempotent write seam.
# It can't fold into ``runtime_flags.py`` (store import → circular) or
# ``registry.py`` (kept store-free by design). A feature, justified.
# 2026-07-04 abort-mechanism rework (no tracked dim moved, concepts down):
# 2 elimination gates (dominance + equivalence) folded into ONE paired-margin
# gate; the online per-sample CAT re-fit concept deleted (next_sample closure
# chain, posterior_from_outcomes, per-step timelines) → the shared round order
# is one pure function. Net ≈ −190 LOC in application code. config_leaf_fields
# unchanged: online_reorder/dominance/equivalence stay on-disk (inert / folded)
# pending the held config-surface-shrink pass.
# 2026-07-08 config-surface shrink: ``config_leaf_fields`` 39->38 — deleted the
# INERT ``SelectionMechanisms.online_reorder`` toggle end-to-end (field + the
# ConfigOverrides fork-delta twin + its dead-predicate ``selection_basis_pair``
# coupling + estimand entry + wire schema + fork sel_updates). The within-round
# order is always the deterministic shared ``build_round_order``; the online
# per-sample re-rank it toggled was already deleted. Subtraction, baseline falls.
# then ``modules`` 300->309 / ``init_files`` 54->58 / ``reexport_shims`` 41->45:
# the L4 Lab subsystem (statistically-rigorous L4). New packages
# ``application/meta_champion`` (reduce the pp-self corpus to a ranked champion
# table + coronate/promote), ``application/resource_matrix`` (the target-model ×
# dataset capability grid), the ``domain/l4`` blocked-paired verdict,
# plus ``matrix`` / ``champion`` CLI verb packages. Each is a concept the loop
# lacked: select the overall-best meta-prompt, match models to datasets, a
# rigorous per-round read. A feature, justified.
# 2026-07-05 L4 variance fix: ``config_leaf_fields`` 37->36 — deleted
# ``OptimizationConfig.noop_probe`` end-to-end (the in-loop no-op probe arm read
# noise as a win; killed, no config is ever re-measured mid-run). Subtraction, so
# the baseline falls. ``modules`` 309->311: the noise-floor capability survives
# ONLY as a fenced debug diagnostic (``application/noise_floor.py`` +
# ``presentation/cli/commands/noise_floor.py``, mirroring ``verify``) — a new CLI
# verb, not a loop feature (no config field, no L1 injection). A feature,
# justified, so the baseline rises.
# 2026-07-05 successive-halving replication: ``config_leaf_fields`` 36->37 — a
# deliberate OPT-IN operator knob ``OptimizationConfig.replicate_survivors``
# (default 0 = off in the distributable): re-measure survivors with force_fresh
# for independent draws the estimators average, killing the idiosyncratic inner
# draw CRN cannot. A dev-stage feature, justified, so the baseline rises.
# 2026-07-05 lives ("hearts") round budget: ``config_leaf_fields`` 37->39 — a
# deliberate OPT-IN operator knob ``OptimizationConfig.lives`` (nested
# ``LivesConfig{start, cap}``, default None = off): improvement-banked variable
# round length replacing the fixed ``max_rounds`` boundary. A feature, justified,
# so the baseline rises (two leaves: start, cap).
# 2026-07-08 schema-rename lever, paid for: ``config_leaf_fields`` net 39->38. In, one
# knob -- ``OptimizationConfig.schema_field_rename`` (may THIS campaign's L1 rename a field
# on the inner ``l1_generate``'s output schema). A field NAME is the wire contract, so
# unlike the always-free ``description``/order levers it needs a lock. Out, two:
# ``deterministic_dominance`` + ``equivalence_elimination`` were two names for one gate's
# two corners, ORed at a single call site, and are now one ``PoBBConfig.margin_elimination``.
# 2026-07-10 dead-surface pass: ``reexport_shims`` 45->44 —
# ``application/optimization/__init__.py`` re-exported ``Cycle``, which every one of its
# 20+ consumers already imports from the leaf; the shim had zero callers.
# ``settings_const`` 16->15 — ``FAILURE_WARNING_PREVIEW`` was referenced nowhere, not even
# inside its own module. Both subtractions, so the baseline falls.
# 2026-07-11 storage Arc 3 (measurement index → append-only): ``modules`` 311->313 — two
# genuine additions: ``store/read_model.py`` (the append-only JSONL primitives that retire
# read-whole/O(n)-scan/rewrite-whole — save at n=1000 dropped 88ms->2.6ms) and
# ``cli/commands/reindex.py`` (the on-demand index-rebuild verb). Both earn their line.
# 2026-07-11 the knob declares itself: ``modules`` 313->312 and ``config_leaf_fields``
# 38->37. A config leaf was declared three times — the Pydantic field, ``config_diff``'s
# ``_FIELD_SCOPES``, ``config_coupling``'s ``_KNOB_ESTIMANDS`` — and walked four times, by
# walkers that disagreed (``lives`` was one leaf or two depending who asked). Scope +
# estimands now ride the field as ``Knob`` metadata, so the two side tables (and their two
# walkers + two import guards) collapse into one derived registry in ``application/knobs.py``,
# which replaces both modules. ``config_leaf_fields`` is now ``len(KNOBS)``: the registry IS
# the taxonomy, so the ledger stops carrying a fourth opinion (it counted ``dataset_split``
# as two leaves; the knob is one). Subtraction, so both baselines fall.

# 2026-07-11 prompt building-block library: ``config_leaf_fields`` 37->38 \ ``injections``
# 23->24 — the catalogue of reusable prompt-field values (``config/prompt_variants.json``:
# 42 thinking styles + personas + task intents + answer formats, adopted from
# PromptWizard/Self-Discover and our own runs) reaches L1 again. It shipped as data, its
# loader was deleted as an orphan, and nothing had read it since. One knob
# (``OptimizationConfig.prompt_block_catalogue``: guidance | restrict | off), one injection
# (``prompt_block_catalogue``), one loader module. This is the only channel that hands L1
# reusable prompt MATERIAL rather than statistics about material — every other cross-run
# panel carries numbers. A feature, justified, so the baseline rises.
# 2026-07-11 MCTS over the lineage: ``settings_const`` +1 (``UCB_EXPLORATION_C``) — the
# backprop fold + UCB rewind rule (``application/mask/backprop.py``) that close the last
# two of MCTS's four phases. Deliberately NOT a config leaf: one rewind costs a whole
# cycle, so the exploration weight is a property of the search, not a per-run dial. The
# ``ForkProposal.round_offset`` field is DELETED in the same pass (the layer no longer
# picks the target, UCB does), and ``is_leader_eligible`` moved down to ``domain/results``
# — a pure predicate that had been filing the whole optimization package into every
# module that merely wanted to read a candidate's fate.
# The three FALLS below are the knob-registry refactor's, banked here rather than left
# loose: a baseline above actual is a ratchet that has stopped ratcheting, and would
# silently re-admit the two shims it already paid to delete.
# then FALLS from deleting the dead void-writer (L2 authored ``l1_supplemental_rules`` /
# ``l1_situational_examples`` into a channel ``l1_generate`` never rendered) + the dead
# ``intractable_samples`` panel: ``injections`` 24->21, ``opt_search_point_fields`` 27->25,
# ``modules`` 313->312 (``auto_rules.py`` deleted).

# 2026-07-13 the inner runner becomes a package, and its config declares itself: ``modules``
# 315->317 / ``init_files`` 59->60 / ``reexport_shims`` 42->43. A 1105-line orchestration file
# split along its real seam — ``inner/tasks.py`` (the panel an outer dataset DECLARES) vs
# ``inner/cycle.py`` (how one cell is RUN). What the ledger cannot see is the subtraction that
# paid for it: ``inner_tasks.json`` was the ONLY config in the package with no schema, hand-parsed
# through a ``.get()`` ladder guarded by a hand-written ``_REQUIRED_BENCH_KEYS`` tuple — the
# membership-test-over-NAMES bug class ``promptpotter/CLAUDE.md`` names. With nothing able to
# reject a key, it had grown two nobody read: an 8,050-char ``description`` (a comment field
# invented because JSON has no comment syntax — it had drifted into restating the proxy law, and
# the guidance someone wrote there "so an LLM would read it" reached no LLM) and a ``dataset_path``
# no code has ever resolved. `extra="forbid"` at every level makes both unrepresentable rather
# than merely deleted, and the type replaces the hand-rolled validator + its coercion ladder.
# 2026-07-13 the L4 law gets one home: ``modules`` 313->315 / ``init_files`` 58->59. Raised
# DELIBERATELY, and the same argument as `shared/instrument.py` below: the ledger counts
# modules, not scattered law, so it can see only the files that appeared. What it cannot see is
# what was subtracted — the L4 proxy law was PURE DOMAIN LOGIC (reads a `CycleResult`, returns
# an `OuterSampleProxies`; no I/O, no session, no store) living 500 lines deep inside a
# 1105-line orchestration file in the APPLICATION layer, one package away from the type it
# produces. That distance is why it drifted from its own type's docstrings, from the two docs
# that restated it, and from an 8k-char JSON blob that restated it again. `domain/l4/` is one
# findable package — `proxies` (what one inner cycle says about a meta-prompt: floor / exclude /
# measure) and `verdict` (what a round of them says about a variant) — in the layer that
# structurally forbids the I/O the law must never grow. One concept, one home; the module count
# is the price of making it findable.
LEDGER_BASELINE = {
    # 312 -> 313: `shared/instrument.py`. Raised DELIBERATELY, and it is the honest number to
    # argue about: the pass it pays for removed three ambient ContextVars and three public
    # setters (`_INNER_DEPTH`/`MAX_INNER_DEPTH`, `_OPTIMIZER_CONFIG_OVERRIDES` +
    # `set_optimizer_config_overrides`, `_EVIDENCE_EPOCH` + `set_evidence_epoch`) spread across
    # three layers, and replaced them with ONE declared mode — a cycle either IS a measurement
    # instrument, with every hermetic property bound together, or it is not. The ledger counts
    # modules, not ambient globals, so it can see only the file that appeared. It cannot be
    # folded into an existing `shared/` module without making that module mean two things.
    "modules": 317,
    "init_files": 60,
    "reexport_shims": 43,
    "config_leaf_fields": 38,
    "settings_env": 16,
    "settings_const": 14,
    "opt_search_point_fields": 25,
    "prompt_string_fields": 6,
    "injections": 21,
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
