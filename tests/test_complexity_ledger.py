"""Ratchet: the package's conceptual surface never moves unexamined.

This is the enforcement teeth behind the <surface-ledger> doctrine
(docs/developer/conventions.md § Reasoning doctrine). The
recurring AI blind spot is additive-but-safe "refactors" that grow the module +
import surface while claiming to simplify; a prose rule gets ignored, a red test
does not. Each dimension is pinned to a baseline and asserted ``<=``, so a raise
stops here and asks for a reason — it is not forbidden. Growing the surface means
editing the baseline up with the reason recorded below (a feature, or a shape that
makes the codebase quicker to develop). What is banned is growing the surface
while calling itself a refactor.

When a deletion legitimately LOWERS a dimension, lower its baseline in the same
commit (surface-ledger rule 4: "lower the baseline to lock the win") so it cannot
drift back. The baseline records where the surface stands; it is not a target to
reach and halt at.
"""

from promptpotter.diagnostics.complexity_ledger import compute_ledger

# Captured 2026-06-19 after the unification-phase subtractions. Every edit to a
# number carries a reason: down = a deletion earned it, up = a feature or a
# develop-speed win. The raises named below are the precedent, not exceptions.
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
# estimand + which knobs collide, plus a CLI table (since deleted). Lifts
# the "deferred-with-the-flip" knob interactions out of spec prose into one
# machine-checked registry, read by the preflight gate and the webapp config-map
# panel. A feature — operator-requested collision visibility).
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
# 2026-07-13 feed the generator: ``injections`` 21->23 — ``failing_samples`` + ``mutation_memory``
# on `l1_generate`'s floor. Raised DELIBERATELY, and paid for in the same pass:
# `prompt_block_catalogue` under `guidance` now renders only the blocks adopted from our own runs
# (`restrict` still renders the whole library, because there it IS the value space), which cuts
# ~4.9k chars — more than the two panels cost. The ledger cannot see that trade; it counts panels,
# not bytes. They exist because the generator was blind in two ways no amount of tuning fixes: it
# could not see a single failing sample (it read a prose compression of the misses and had no way
# to check it, nor to know WHICH misses were winnable — ordering them on the cycle's locked δ ruler
# is the one thing in that panel L1 cannot compute for itself), and it had no record of its own
# prior attempts, so a later round could re-propose a mutation already measured and lost.
# 2026-07-13 the collapse detector: ``injections`` 23->24 — ``answer_distribution`` on
# `l1_generate`'s floor. Raised DELIBERATELY. Measured: the inner justlogic pipeline answers
# "Uncertain" to 80-96% of samples, and its accuracy TIES the score a constant "Uncertain" would
# earn — it had degenerated into a stub, and every panel we already had said only that accuracy
# was low. The generator's response, round after round, was to rewrite the anti-hedging sentence
# that was already verbatim in the origin schema, louder. No amount of better failure evidence
# fixes that, because the missing fact is not in the failures: it is in the HITS, in the shape of
# the answer set as a whole. This panel is ~250 chars, renders empty on free-text answer spaces,
# and is the only thing in the prompt that can tell a pipeline that reasons from one that gave up.
# 2026-07-18 feed the L4 generator: ``injections`` 24->25 — ``inner_narratives`` on `l1_generate`'s
# floor. Raised DELIBERATELY. Each outer sample of an L4 round IS a whole inner campaign, and the
# authored story of what it tried / steered on / where it stalled (`_inner_narrative`) was reaching
# only the outer CRITIQUE (`sample_transcripts`), never the outer GENERATOR — which saw one scalar
# per-seed delta and re-proposed what the inner loop had already measured (the overnight flat loop).
# The panel is silent off the recursion (no `reasoning_trace` on the row), so it costs a normal
# campaign nothing.
LEDGER_BASELINE = {
    # 312 -> 313: `shared/instrument.py`. Raised DELIBERATELY, and it is the honest number to
    # argue about: the pass it pays for removed three ambient ContextVars and three public
    # setters (`_INNER_DEPTH`/`MAX_INNER_DEPTH`, `_OPTIMIZER_CONFIG_OVERRIDES` +
    # `set_optimizer_config_overrides`, `_EVIDENCE_EPOCH` + `set_evidence_epoch`) spread across
    # three layers, and replaced them with ONE declared mode — a cycle either IS a measurement
    # instrument, with every hermetic property bound together, or it is not. The ledger counts
    # modules, not ambient globals, so it can see only the file that appeared. It cannot be
    # folded into an existing `shared/` module without making that module mean two things.
    # then ``modules`` 317->318: the sealed sub-principal grant store
    # (``infrastructure/identity/grants.py``) — the delegation authority file
    # (ADR-0005 §1) that turns an authenticated user into a delegator's
    # attenuated sub-principal. It lives in the identity zone beside the allowlist
    # (a delegate cannot write its own grant); attenuation is enforced at read
    # time (grant ∩ owner set). A first-class security feature — user-minted
    # sub-users with a bounded capability slice — so the baseline rises. It can't
    # fold into ``allowlist.py`` (email gate) or ``migration.py`` (default-claim
    # rebind) without making either mean two things.
    # 318 -> 319 -> 318 (2026-07-17): ``infrastructure/store/lineage_views.py`` arrived as the
    # ONE owner of the lineage tree (``course -> candidate -> (course | sample)``, alternating
    # at any depth), and ``routers/campaigns/lineage.py`` (5 wire models + 10 projection
    # helpers) paid for it in full. That genealogy had FIVE witnesses on disk and no owner, so
    # every surface reassembled its own and they disagreed about the same node. One owner, at
    # no net module cost. The raise was booked here before the deletion landed rather than
    # taken quietly, because a half-migrated tree living beside the live one is exactly what
    # this ratchet exists to make loud.
    # 318 -> 319 (2026-07-17): ``infrastructure/store/session_pointer.py``. Raised
    # DELIBERATELY, and it buys the death of a cycle GENERATOR. ``store/__init__`` used to
    # eagerly import all ten leaf stores, so importing any leaf (``store.io`` /
    # ``store.layout`` — both pure, neither able to cycle alone) executed it and dragged in
    # ``CampaignStore``, which imports back up to ``runtime_flags`` and ``ledger``. Whether
    # that exploded depended on which module the process reached FIRST: entry points hit
    # ``store`` first and hid it, so THREE back-edges were cut to dodge it — and CI's
    # ``scripts/build_ts_types.py``, which reaches ``ledger`` first, went red anyway.
    # The only reason that ``__init__`` needed a body was the active-session pointer; moved
    # here, the aggregator empties, and all three hacks revert to plain imports (the
    # acceptance test for the pass). It cannot fold into ``session_store.py``: that is an
    # instance store over ``sessions/{id}/session.json``, this is tenant-keyed free
    # functions over ``.workspace/active_session.json`` — a different file, root and shape.
    # ``reexport_shims`` 9 -> 8 pays for it, so TOTAL is flat: the win is that a fourth
    # back-edge is now impossible rather than likely, which this ledger cannot count.
    # 319 -> 312, ``init_files`` 60 -> 56 (2026-07-17): four packages that existed to hold a
    # single thing were collapsed to one module each — ``presentation/views/render/``
    # (``text`` + its only caller ``sp_diff``, one external consumer),
    # ``api/middleware/command_dispatcher/`` (``dispatcher`` + ``helpers``, which had zero
    # importers outside it), ``application/output/`` (``writers`` + ``review``), and
    # ``projections/event_stream/`` (one module, one importer). A package around one call
    # chain is a directory a reader must open to learn there was nothing to choose.
    # then 312 -> 311, ``init_files`` 56 -> 55: ``pobb/elimination/`` was a package whose
    # parent package held nothing else — two nested dirs around two leaves. The leaves moved
    # up to ``pobb/{checks,classification}.py``. NOT fused into one module: ``classify_result``
    # has more readers (scoring, metrics) than the stop rules do, and a general result
    # classifier living in a module named for a budget-allocation algorithm would read wrong
    # at every one of those call sites. Two honest concepts, one package, no wrapper.
    # then 311 -> 310, ``init_files`` 55 -> 54 (2026-07-17): ``dispatch/hub/`` was dissolved —
    # ``bundle``/``facade`` rose to ``dispatch/``, ``injections/`` to ``dispatch/injections/``.
    # It was the repo's ONLY d5 path, in the loop's most-read zone, and the hop bought nothing:
    # the package name said what its parent (``dispatch``) and its own class (``DispatchHub``)
    # already said. ``injections/`` stays a package — it is a real concept with a registry, not
    # duplication. The ``@signal`` side-effect imports are explicit module imports, not a
    # package walk, so they survive the move untouched.
    # then ``init_files`` 54 -> 52 (2026-07-17): ``cli/commands/{champion,matrix}`` were
    # packages containing exactly ONE file — their own ``__init__``. Every other command in
    # that directory is a module (``new``, ``verify``, ``ab``, ``reset``, …), so the two dirs
    # bought a reader one hop to learn there was nothing to choose, and made the listing lie
    # about which commands have parts. ``sweep/`` stays a package: it genuinely has four
    # (``panel``, ``rank``, ``time_to``, ``_common``). The import path is unchanged either way
    # (``commands.champion`` resolves to package or module alike), so this cost zero repoints.
    # then 310 -> 311 (2026-07-17): ``domain/strict_model.py``. Raised DELIBERATELY, and it is
    # a one-class module on purpose: ``StrictModel`` is the base 168 of the package's 172
    # models now inherit, so it must sit below all of them and import nothing but Pydantic.
    # It cannot fold into an existing ``domain/`` module without every model importing that
    # module's other concerns, and ``shared/`` excludes it by its own charter ("no service or
    # model dependencies"). What the module buys: Pydantic's default is ``extra="ignore"``, so
    # the posture was not decided anywhere — it was re-decided, or forgotten, 172 times across
    # 119 hand-copied ``model_config`` lines. 65 of those lines are now gone (the base says it
    # once), and the default inverted: forbid is what you get by not thinking, lax is what you
    # write down. The bug it pays for shipped: ``ObservationMapping(obs_key=…)`` — the field is
    # ``output_field`` — rode a real ``pipeline.json`` for months, silently a no-op, with ruff,
    # mypy and pytest all green.
    # then 311 -> 309 (2026-07-17): the ``champion`` verb was deleted — ``cli/commands/
    # champion.py`` + ``application/meta_champion/champion.py``. Operator call, made against
    # a correction: the ChampionConsole panel CANNOT land a winner (its row ops are
    # ``disabled`` placeholders whose tooltips say "run: champion apply"), so the verb was
    # the only write path into ``datasets/_optimizer/pipeline.json``. Deleting it makes
    # graduation a deliberate hand-edit — a once-per-winner action that did not earn ~560
    # lines of promote/coronate/apply/replay machinery, none of which had ever run: no
    # ``champion.json``, no ``meta_champion/registry.json`` exists on disk anywhere.
    # ``reducer.py`` SURVIVES — the API recomputes it live for the panel. What went with the
    # verb: the registry's persistence (``read``/``write_registry``, ``registry_path``) had no
    # other caller, and ``CandidateRow.status`` collapsed to the constant ``"provisional"``
    # once nothing could write ``"confirmed"`` (coronate) or ``"champion"`` (the pointer) —
    # a field with one reachable value is not a field.
    # then 309 -> 302 (2026-07-17): the ``sweep`` VERB (``cli/commands/sweep/`` 5 modules +
    # ``application/sweep/toolkit.py``) and ``diagnostics/config_map.py``. Both operator calls.
    # The sweep verb was the SECOND harness minting per-variant forks to A/B prompts -- the
    # first is ``new --sweep-batch`` (``application/sweep/sweep_runner.py``, "one fork per
    # OperatorSweepFile via _mint_fork"), which SURVIVES: it rides the shipped ``new`` mint
    # seam and the shared fork primitive instead of hand-rolling its own batch ids. Neither
    # had ever run -- no archive/sweeps/, no sweep_id, no OPERATOR_SWEEP fork exists on disk
    # (three search shapes) -- so this is the "fold into the canonical mechanism, never add
    # beside it" rule applied to a redundancy that never got exercised. ``config_map.py`` was
    # the THIRD hand-maintained rendering of the ``knobs.py`` table; the preflight gate and
    # GET /campaigns/{id}/config-map both survive untouched, and knobs.py stays the SoT.
    # then 302 -> 303 (2026-07-18): ``intelligence/earned_blocks.py`` — the earned prompt-block
    # library (dispatch-first phase). Mines run history for short field values that earned
    # CREDIBLE lift on the same answer-space shape, replacing the static seed catalogue the
    # ``guidance`` block mode served to every task. A feature: it turns a low-value, task-
    # mismatched panel into earned-or-silent signal, so the baseline rises.
    # 303 -> 301 (2026-07-20 debt sweep): deleted two zero-importer dead modules —
    # ``application/datasets/traces.py`` (the potter-trace loader; superseded by L4 inner-cycle
    # recursion, never registry-routed) and ``infrastructure/tracing/replay.py`` (the historical
    # Langfuse backfill; no CLI verb, no caller). Subtraction, so the baseline falls.
    # 301 -> 300 (2026-07-26): withdrew the ``llm_only`` CONNECTOR
    # (``connectors/llm_only.py``, the 295->296 feature raise noted above). It shipped and
    # then sat at ZERO dataset adopters for its whole life — every single-node benchmark
    # names an ``llm_only`` *node* inside a ``termnorm`` pipeline and still needs the
    # server. Its in-process answer extraction duplicated TermNorm's ``_step_llm_only``
    # over the wire, and its own docstring warned the two arms "must agree on shape … or
    # one measures a different thing than the other" — a standing divergence risk carried
    # for a path nobody ran. Operator call: the single-node case is served by the TermNorm
    # connector accepting an ``llm_only`` pipeline, so ``llm_only`` is now a node name
    # only and the connector/sentinel name collision is gone. Subtraction, baseline falls.
    # 300 -> 296 (2026-07-26): retired the resource-matrix arc — the `matrix` verb
    # (``presentation/cli/commands/matrix.py`` + ``_add_matrix_args``), the
    # ``application/resource_matrix/`` package (3 modules), the ``GET /resource-matrix``
    # route, ``CapabilityMatrixPanel`` + its hand-written TS types, and the ``.cap-*``
    # CSS. Structurally live end-to-end, but **no ``resource_matrix.json`` has ever
    # existed in this repo** — the panel only ever rendered its empty state, so the
    # write path (`matrix measure`) was never once run. Same "never ran" evidence that
    # retired the `champion` verb (2026-07-17) and the `sweep` verb. Operator call.
    # Consequence recorded honestly: ``classify_band`` + ``constant_answer_floor`` went
    # with it, so the CONSTANT-ANSWER-floor criterion in
    # ``docs/operations/dataset-selection-rationale.md`` is now read off the live
    # ``answer_distribution`` panel instead (the enforcement never fired anyway —
    # reaching it required the unrun verb). Subtraction, so the baseline falls.
    "modules": 296,
    # 52 -> 51 (2026-07-17): ``cli/commands/sweep`` went with the sweep verb.
    # 51 -> 50 (2026-07-26): ``application/resource_matrix/__init__.py`` went with the
    # retired arc above.
    "init_files": 50,
    # 43 -> 9 (2026-07-16): 34 package ``__init__`` files that did nothing but re-export a
    # leaf's names were emptied to docstring-only namespace markers, and their ~190 consumer
    # sites now import the leaf they actually wanted. A shim is a hop a reader must take and
    # a second place a name lives; deleting one subtracts both.
    #
    # 9 -> 8 (2026-07-17): ``infrastructure/store`` left the floor — see the ``modules`` note
    # above. It was only ever a survivor because the active-session pointer lived in its body.
    #
    # 8 -> 6 (2026-07-17): ``cli/commands/{champion,matrix}`` left the floor — see the
    # ``init_files`` note above. Their bodies are unchanged; they are simply modules now, so
    # the counter stops seeing an ``__init__`` at all. A body in an ``__init__`` was the only
    # thing that ever made them look like shims.
    #
    # **The 6 survivors are the floor — they are not shims, and emptying them breaks the app.**
    # Don't re-propose them:
    #   * ``connectors`` — IS the connector registry (import-time guards).
    #   * ``presentation/api/routers/campaigns`` — IS the route registry: its submodule imports
    #     run the ``@campaigns_router`` decorators, so emptying it mounts a router with ZERO
    #     routes. The one that reads like a pure re-export and is not.
    #   * ``shared``, ``application/scoring/formula``, ``application/views/render`` — real
    #     code in the body (the counter sees ``__all__`` + an import and says "shim"; it is
    #     wrong).
    #
    # 6 -> 5 (2026-07-17): ``cli/commands/sweep`` left the floor with the sweep verb itself.
    "reexport_shims": 5,
    # 38 -> 39 (2026-07-19): a deliberate new operator knob -- ``CampaignConfig.sp_budget_origin``
    # (origin eval breadth; None = sp_budget_ttest). Lets the origin be scored on MORE bank
    # samples than each candidate: origin theta is the term every delta subtracts and its rows
    # are shared cache, so breadth there is cheap precision, while candidate breadth is paid
    # per-candidate. First user: the L4 inner instrument (inner_tasks.json::n_samples_origin,
    # origin 40 vs candidates 28). A feature, justified, so the baseline rises.
    "config_leaf_fields": 39,
    "settings_env": 16,
    # 14 -> 15 (2026-07-18): ``ANSWER_SPACE_CAP`` moved out of ``dispatch/bundle.py`` into
    # settings — now shared by the ``answer_distribution`` collapse detector and the earned-
    # block library's task-fit signature, so both draw the enumerable-answer-space line from one
    # constant. A relocation into the shared home, not a net-new concept (it left bundle.py).
    "settings_const": 15,
    "opt_search_point_fields": 25,
    # NEW dimension (2026-07-17), landing at 73 — a bare ``x: Any`` parameter whose real type
    # exists and simply was not written. It belongs on a CONCEPTUAL-surface ledger because a
    # type the checker cannot see is surface a reader must carry in their head: the docstring
    # says `OptSearchPoint`, the signature says `Any`, and only one of those is checked.
    # It earns the ratchet because `strict = true` STRUCTURALLY cannot catch it —
    # `disallow_untyped_defs` is satisfied (`Any` IS an annotation) and `warn_return_any` is
    # defeated by any expression that unions `Any` with a concrete type. That is not
    # hypothetical: `resp.content or ""` (`connectors/llm_only.py`) read as a cosmetic
    # no-op for months while doing nothing but silencing `no-any-return`; deleting the `or`
    # made the error fire instantly. `disallow_any_explicit` is not the fix — it rejects
    # `dict[str, Any]` (honest, for raw JSON) exactly as hard.
    # 95 -> 73 in the landing pass: the 22 whose type was already named in-repo, incl.
    # `score_search_point(opt_sp: Any)` — the single scoring gateway — and
    # `compute_composite_fitness`, whose own signature typed `pipeline_schema: PipelineSchema`
    # three lines above. Typing them was not cosmetic: it surfaced that `escalate_l2` really
    # does receive `PipelineSchema | None`, a fact the `Any` had hidden from every reader.
    # Like ``reexport_shims``, this marches to a FLOOR, not to zero — ~30 are honest (raw JSON
    # pre-parse, provider SDK payloads behind `follow_imports="skip"`), and `**kwargs: Any`
    # is excluded outright.
    # 73 -> 69 (2026-07-17): four bare ``Any`` params rode the deleted sweep verb +
    # config_map. Subtraction by deletion, not by retyping -- the debt left with its code.
    # 69 -> 65 (2026-07-20 debt sweep): four bare ``Any`` params retyped to the concrete
    # type their sole caller already passes -- ``_archive_measurement_to_qm(m: Measurement)``
    # (verify.py), ``finalize_checkin_to_active(cycle_plan: CyclePlan)`` (session.py),
    # ``Cycle.start(round_scorer: RoundScorer | None)`` (cycle.py), and
    # ``_stash_rebase_request(proposal: ForkProposal)`` (firing.py). Subtraction, baseline falls.
    "any_params": 65,
    # NEW dimension (2026-07-17), landing at 4 — a Pydantic model that does NOT end up
    # ``extra="forbid"``, so an unknown key is dropped instead of raised. 106 before the
    # ``StrictModel`` migration, 4 after. It is a conceptual surface because the alternative
    # to counting them is the state we were in: nobody could say which models were strict,
    # and the answer was per-model archaeology through 119 hand-copied ``model_config`` lines.
    # Unlike ``any_params``, this one does NOT march to zero — the 4 are the floor, each
    # naming its reason on the model itself:
    #   * ``RoundResult`` / ``ScoredCandidate`` — ``@computed_field`` round-trip. Pydantic
    #     serializes a computed field OUT and rejects it back IN, so ``model_dump()`` writing
    #     ``round_id``/``scoreboard``/``ci_lo``/``ci_hi`` into the round file and
    #     ``model_validate()`` reading that same file are only compatible while extras are
    #     ignored. Verified, not assumed: forbid breaks all 78 real round files on disk.
    #   * ``NodePromptInfo`` — the BACKEND owns that sub-object's vocabulary (``family``,
    #     ``description`` ride real ``pipeline.json`` files) and PP reads a subset by contract.
    #     A backend PP does not own must be free to describe itself without crashing PP.
    #   * ``Sample`` — a dataset row carries whatever columns the operator's file had.
    # ``ObservationMapping`` is deliberately NOT on this list though it also parses
    # ``pipeline.json``: PP owns that vocabulary, so an unknown key there is a typo, which is
    # the whole reason this arc exists. All 10 committed ``pipeline.json`` files pass forbid.
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
