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
``<surface-ledger>`` rule 1 points at — **and which of its members are a deliberate FLOOR
rather than debt**, because that is a fact about this number, not about the code that
computes it. The matching ``_count_*`` in ``promptpotter/diagnostics.py`` states only what
is counted, and only where the name cannot; do not restate either half in the other.
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
    #
    # 302 -> 310 (2026-08-08): eight modules, all of them a name that had stopped describing
    # its contents. NOT a refactor and not labelled one — each split is justified by root
    # `CLAUDE.md` § STOP, and the measured read cost only set the ORDER. `behavior_base.py`
    # (the check vocabulary `l2_behavior` had to import out of `l1_behavior`, re-declaring
    # `CheckFn` verbatim beside it); `review_md.py` (the pure renderer whose module carried
    # TWO `__all__`, the second silently shadowing the first, so its one externally-called
    # function was never exported); `pipeline_overlay.py` + `candidate_diff.py` (two halves of
    # `opt_search_point.py` containing zero references to its models, with disjoint callers);
    # `l1_wire_schema.py` + `l1_invariants.py` (schema EMISSION, which neither rejects nor
    # scores and so belonged in `dispatch/`, and the round-local collapse gates, which need no
    # schema at all); `selection.py` + `diagnostics.py` (out of `metrics.py`, a bag name over
    # three concerns). Two re-exports were DELETED on the way — `INVARIANT_REASONS` from
    # `l1_strict` (claimed three call sites, had one) and `find_gt_rank`'s dead `__all__`
    # entry — and `build_campaign_emitter` moved out of `origin.py`, which builds no projection.
    #
    # 310 -> 316 (2026-08-08): six more, same rule as the eight above — a name that stopped
    # describing its contents. `resume_and_fork/repair.py` (the cut/correct/re-bank machinery;
    # `resume.py` now asks only whether the run DIVERGED, and the two are asked independently —
    # the repair runs whatever the config diff says). `domain/dashboard_rows.py` (`RoundSummary`
    # + `RoundSummaryCandidate`: one importing package, `live_dashboard/`, against 62 for
    # `results.py`; NOT named `round_summary.py`, because the projection that builds them
    # already owns that name a layer up). And `routers/datasets/` as four — `_router`, `index`,
    # `ingest`, `leaderboard` — the `routers/campaigns/` pattern, for the same reason: one
    # resource carrying several unrelated concerns.
    "modules": 316,
    #
    # 47 -> 48 (2026-08-08): `routers/datasets/__init__.py`. A router package cannot avoid one —
    # the submodules' decorators must run before the populated router is re-exported.
    "init_files": 48,
    # A FLOOR: none of the 5 is a shim, and emptying any of them breaks the app. `connectors`
    # and `presentation/api/routers/campaigns` ARE registries — the submodule imports run the
    # `@campaigns_router` decorators, so emptying the latter mounts ZERO routes. `shared`,
    # `application/scoring/formula` and `application/views/render` hold real code in the body,
    # which a text test cannot see.
    #
    # 5 -> 6 (2026-08-08): `routers/datasets/__init__.py`, the SIXTH — and deliberately the
    # same sanctioned kind as the fifth (`routers/campaigns/__init__.py`), not a new one. A
    # FastAPI router package has no other shape: the alternative is `main.py` importing three
    # submodules purely for their decorator side effects, which is the same coupling with
    # nothing naming it. A seventh of a DIFFERENT kind still needs its own argument.
    "reexport_shims": 6,
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
    #
    # 38 -> 39 (2026-08-07): ``hard_sample_order``. The hard-sample leaderboard had THREE
    # orderings and no owner — δ_s served by `/preview`, `build_round_order` in the engine,
    # and an info-gain sort the browser invented and applied over the served one. Naming the
    # key is what lets one served rank replace all three, and it is a `Estimand.DISPLAY` knob
    # beside `headline_metric`: it picks the order a human READS and reaches no decision.
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
    # 65 -> 66 (2026-08-06): `_keep_known_keys._v`, whose `Any` is the same one its peer
    # `_truncate._v` carries — a Pydantic `BeforeValidator` runs before coercion, so the
    # value it inspects is genuinely untyped. Typing it narrower would be a claim the parse
    # boundary cannot make.
    # 66 -> 65 (2026-08-06): `ab_replay_cycle`'s local `_rescore(items: Any)`. Its rescore now
    # happens inside the replay verdict, on a typed `RoundResult` the fold hands it, so the
    # `Any` that existed only to swallow "results or all_candidate_results, whichever" is gone.
    #
    # 65 -> 64 (2026-08-08): `build_campaign_emitter` returned `Any` because a function-local
    # import kept `LiveDashboardView` out of its own signature. Moving it to `run_observers.py`,
    # which already imports that class, let the real type be written — and the honest
    # `LiveDashboardView | None` immediately surfaced that `build_run_observers` binds the
    # result to the ledger unguarded. The `Any` had been hiding a `None` nothing checked.
    "any_params": 64,
    # New (2026-08-05). Lowering it is what makes the cycle-index modelling question
    # (`docs/specs/code-debt-cleanup.md`) falsifiable rather than a judgment call.
    # 73 -> 74 (2026-08-06): `modal_answer_share`. A deliberate raise for the CONTINUOUS
    # reading of a question the package could only ask as a bool — `is_answer_collapsed`
    # needs a literal single label, so a model answering one label 95% of the time passed
    # every check and graded `healthy`. It reports and never gates: hedging is the failure
    # the loop corrects, so the number's value is its round-over-round series.
    # 74 -> 77 (2026-08-06): `merge_known_outcomes` moved here from
    # `application/optimization/cycle.py`. Three maps that already existed, now counted in this
    # layer — a relocation, not a new surface. The pool is folded by the live loop, by resume,
    # and by the mask record rebuilt from disk; leaving it beside the loop obliged a READ path
    # to import the whole loop to re-fold what it had already read.
    #
    # A high FLOOR. Honest: the pipeline overlay (`opt_search_point`, `pipeline_parsing`,
    # `pipeline_schema`, `search_point`, `connector`), a node's output (`rendering`,
    # `validators`), a dataset row (`sample`). Retirable: `results`, `results_health`,
    # `l4.verdict`, `measurement_provenance` — rows of PP's OWN on-disk shapes, taken as bags.
    # 76 -> 77 (2026-08-11): `view_fields` moved from `escalation/state.py` into `domain/`,
    # beside the `PhaseRecord` it reads. Its `dict[str, Any]` return came with it — a MOVE, not
    # a new map: an `infrastructure/` projection needed the same accessor, and owning it in
    # `application/` would have inverted a layer to reach it.
    "domain_any_maps": 77,
    # Moves on 2026-08-06 collapsed to their standing state; `git log -p` is the history
    # layer, and a running tally here is the sweep-log shape the backlog doc names as the old
    # bloat source. What the current number buys, newest first:
    #
    # +3, deliberately: the engine/scorer replay is now a verdict on the mask fold, so it
    # inherits the tree walk (`with_replay` on `load_mask_record`; `_make_replay_verdict`'s
    # bound ruler and its mismatch sink). `ab_replay_cycle` handed one back — the
    # `campaign_store` it no longer needs. What the surface buys is the question the `ab` verb
    # could not previously ask: not which decisions moved, but which FORKS a change
    # invalidates. The fold stops at the first departure per branch, so what it replays is the
    # part of the record the change still carries over, and the rest is named counterfactual
    # instead of re-derived into a history that would not have happened.
    #
    # +2: `_keep_known_keys(allowed)` + its `_v`, closing the one channel where an LLM could
    # grow a rendered surface without limit. `L2ContextOutput.l1_overrides` is a
    # `dict[str, Any]` the layer writes and `_parse_l2` MERGES forward on every fire, while
    # only two keys are ever read — so an invented key was never read, never pruned, and
    # rendered uncapped for the rest of the campaign. Filtering to the vocabulary at parse is
    # what earns that panel its `char_cap=None`: its render is now two scalars by construction.
    #
    # +2: `_drop_class_description(schema, model)`, the `json_schema_extra` hook that keeps
    # class docstrings out of the wire schema. Pydantic hoists a class docstring into the schema's
    # `description`, so our own design notes — module paths, why a lever is not wired — rode
    # every optimizer call: 2088 of `l1_generate`'s 3513 schema chars, 63% of `l2_context`'s.
    # Two params on one inherited classmethod is what buys ~12% off `l1_generate`'s input and
    # ~21% off `l2_context`'s, at all three sites a wire schema is built.
    #
    # +8 (the JustLogic bake-off's follow-up arc). Seven of the eight are ONE parameter each,
    # threading a fact that was already measured to the surface that needed it, and the eighth
    # generalises `_configured_model` to `_configured(node, key)` rather than adding a twin.
    # `reasoning_tokens` on `emit_token_usage`: the wire reports how much of a call was
    # thinking on every round-trip and only the parse-FAILURE path kept it, so the share was
    # legible exactly when it was useless — measured ~94% on the shipped optimizer route,
    # which is why the loop owns a third of an L4 cell's wall-clock. `provider` on
    # `emit_token_usage` / `lookup_rate` / `compute_usd`: a rate belongs to the (provider,
    # model) PAIR, and pricing off the model alone billed our OpenRouter calls at DeepSeek's
    # own list — 1.6x, on the one route that returns no wire cost to contradict it.
    # `answer_modal_share` on `compute_degradation_health` + `modal_answer_share` itself: see
    # `domain_any_maps`. `rows` on `seed_screen._call_cost_and_latency`: the screen spends
    # real money per pass and reported quality only, so the two axes an operator ranks ahead
    # of quality were reachable only by hand-parsing the archive.
    #
    # Earlier the same day: `_sample_series` (serve the aggregate beside the series it
    # aggregates), `cells_dropped` (make a dropped cell loud), `model_cls` on `restamp._process`
    # (the verb was hardcoded to one on-disk model while the forbid flip obliges every one),
    # and `is_electable`. Against those, `backfill_spend_rates` + `_cycle_spend` were deleted.
    #
    # -1: `SampleIndex.register_many`. Its only caller stood behind
    # `if session.scoring.sample_index is not None`, and that field was a `ScorerSetup` default
    # of `None` that nothing ever assigned — a reader with no writer, so the pre-registration its
    # comment described had never once run. `ingest_run` is the sole registrar in fact, which is
    # what the persisted sample index has to be built against.
    #
    # +12, deliberately: the per-run SampleIndex fold is now persisted
    # (`archive_views._sample_fold_path` / `sample_fold_rows` / `write_sample_fold`,
    # `SampleIndex.replay_row`, `AxisIndex._seed_from_fold`). `_seen_runs` was an in-process
    # cursor, so EVERY process start re-read and re-scored the whole dataset slice of the
    # archive — 3.7 s of detail reads plus a compiled-expression eval per measurement at 1076
    # runs, and growing with the archive forever. This is the read-model shape
    # `infrastructure/store/read_model.py` declares for derived-index persistence, not a
    # sidecar; the surface it costs buys a start cost that stops scaling.
    #
    # Marches to a FLOOR: most of this is functions taking their real arguments, and the width
    # `conventions.md` § Code shape REQUIRES — a parameter that changes what a number MEANS
    # taking no default — is the rule working. What a pass can retire is the rest: transport.
    # 4073 -> 4074 (2026-08-06): `_count_docstrings(py_files)`, the one parameter the new
    # `docstrings` dimension below costs. It takes the same file list its ten neighbours do.
    # 4074 -> 4076 (2026-08-07): `_ability_delta(rounds)` — the served headline lift is now read
    # off `cumulative_theta` rather than `best − rounds[0].accuracy`, a fold over the round list
    # rather than two field reads, so it earns a named function — and `establish_campaign_origin`'s
    # `task_context`, which is what makes C0 render on the same basis as the candidates that
    # challenge it. The resolver taking no framing is precisely how they came to differ.
    # 4076 -> 4065 (2026-08-07): framing stopped being THREADED. It is dataset content now â€” check-in
    # commits `task_context.yaml`, and one pure reader (`committed_task_context`) serves identity,
    # C0, seed-screen and the notebook alike. That deletes the parameter from `run_optimization`,
    # `_prepare_run`, `init_optimization_loop`, `_build_and_start_cycle`, `Cycle.start`,
    # `_build_initial_opt_sp`, both launchers and `PreparedCheckinRun`, against the ONE it adds to
    # the resolver. A value carried through eight frames to reach the object that should have been
    # born holding it is the shape this counts.
    # 4065 -> 4066 (2026-08-07): `_sample_lookahead_depth(session)`. A predicate rather than a
    # stored int because the answer changes mid-candidate and is refused for an in-process
    # connector.
    # 4066 -> 4089 (2026-08-07): serving the hard-sample rank. `_LeaderboardPage` +
    # `_resolve_leaderboard_page` + `_page_series` give the two routes that must agree on a
    # page ONE resolver instead of two hand-copied sort keys, and the fields it carries are
    # counted here. The rank needs the Rasch maps AND the per-sample series at once, which is
    # why neither route could hold it and the browser ended up owning the ordering.
    # 4089 -> 4092 (2026-08-08): the SP-diff legend stopped re-printing values and started
    # ADDRESSING them, which the two closures that mint a code could not do without knowing
    # which flat key and which column each value came from — `_get_code` and `_cell` each gain
    # that pair (+4), against `_node_lines` deleted (-1). It buys a real subtraction the ledger
    # does not count: the legend was 39.6% of `logs/latest.log` re-serializing `prompt_fields`
    # once per round, and one measured round-1 diff went 7,627 -> 1,421 chars.
    # 4092 -> 4098 (2026-08-08): `ledger_sample_view` (domain/scoring) and `_view_fields`
    # (escalation/state) — the projection that keeps the ledger to what its subscribers render,
    # and the reader that takes escalation's resume counters off the PERSISTED view instead of
    # the in-memory-only `payload["data"]` they were silently rebuilt from. Both are seams that
    # REPLACE loose dict keys with declared ones (the keep-set carries an import-time subset
    # assert; the counters are now fields on `L2RefineExitView` / `PlanExitView`), so what grew
    # here is the typed surface and what shrank is the untyped one. Plus the three that carry
    # the same projections back over data already written (`compact_cycle_ledgers` and its two
    # helpers, on the existing `restamp --apply` — a second stripper would drift from the
    # writer, so they CALL it). Measured across 60 real ledgers: 102.6 MB -> 44.5 MB.
    # 4098 -> 4100 (2026-08-08): `flush_pending_decisions(cycle, session)` — teardown's half of
    # `persist_round`'s drain. Escalation fires after its round persisted, so a cycle stopping
    # right there had no next round to carry the record: 8 of 89 real L2 fires reached disk with
    # no decision beside them, and the resumed run re-spent budget it had already spent.
    # 4100 -> 4102 (2026-08-08): `refile_round_decisions` + `_document_decisions` — the same
    # verb's reconciler for data already written, since the writer fix is forward-only and 199
    # records on disk named a round that did not make them.
    # 4102 -> 4106 (2026-08-08): the live dashboard's projection seams (`build_candidate_rows`,
    # `_active_node`, `_current_round_nodes`, `_restamp_theta`, `_served`) plus `LedgerAbility`.
    # Every one REPLACES a client-side derivation — the webapp was inferring which optimizer
    # node is running and what a candidate's value±interval is by joining facts written on
    # different events, and each join is deleted. `current_round` stopped being
    # `dict[str, Any]` in an otherwise strict model, which is what let it go so long without
    # answering either question; `LedgerAbility` is the same move on `LedgerRoundClose`, whose
    # `abilities` had to be typed rather than widened to `Any` to carry one extra field.
    # 4106 -> 4103 (2026-08-11): the whisker collapse. `theta_accuracy_ci` and the θ-band
    # override are GONE — one estimator writes the band now, at `candidate_scored`, for every
    # scored arm. The second one could only be computed for arms an election reached, so it
    # made a chart draw two quantities as one band and made the whisker come and go by gating
    # rather than by evidence; `ci_scale` existed only to tell the two apart, and went with
    # them (77 references across 25 files). `RoundBuffer.mark_winner` is the one addition.
    # 4103 -> 4105 (2026-08-11): `_ledger_bands` + `restamp_candidate_bands`, the whisker
    # restamp. Paid to CLOSE a divergence rather than to add a surface: the band had two
    # writers, so the ledger and the round document held different numbers for one candidate
    # on 204 of 448 coordinates. `_iter_cycle_ledgers` costs nothing here and subtracts three
    # copies of the same workspace walk.
    # 4105 -> 4109 (2026-08-11): `SpineCycle` (4 fields) + `load_lineage_spine`, against
    # `MaskRound.cumulative_theta` and `Divergence.node_key` deleted. A deliberate raise that
    # buys a much larger subtraction: the rewind decision used to rebuild the whole campaign —
    # every manifest and every round document of every cycle — to recover four scalars per
    # round, on the escalation path, while a layer waited to fork. Every one of them is a
    # round-CLOSE fact the ledger already stamps, verified equal on 219 of 219 rounds on disk.
    # 4109 -> 4111 (2026-08-11): `rows` threaded into the mask's eligibility test so it can ask
    # `is_electable` — the election's OWN admission rule — instead of the weaker
    # `is_leader_eligible`, which `is_electable`'s docstring says "lets a collapsed arm top a
    # round that refused to crown it". Two params for the difference between a lens that
    # reproduces the election and one that reports a divergence the run never made.
    # 4111 -> 4109 (2026-08-11): `restamp_candidate_bands` + `_ledger_bands` DELETED. A repair
    # verb is only worth its surface while the writer that needs repairing still exists; the
    # band's second writer went with the whisker collapse, and the documents it had already
    # skewed went with the store wipe. Locked down so it cannot drift back.
    # 4109 -> 4115 (2026-08-11): decisions get ONE home. `RoundResult.decisions`, the
    # `DecisionRecord` TypedDict and `ResumeCheckpointRecord.to_dict` are gone — the document
    # copy was their only consumer — and the drain that filled it goes with them. What is added
    # is smaller than what it replaces but is still an add: `scan_ledger_decisions`, the fork's
    # `_copy_parent_decisions` + its `_decision_line` parser, and the `decisions` argument the
    # replay now takes explicitly. The copy is what a fork must answer for ITSELF: every ledger
    # scan reads the physical file, so a record only the parent holds is invisible to the branch.
    # 4115 -> 4117 (2026-08-11): the ELECTION gets a record at its own coordinate — `on_election`
    # writes it, `scan_ledger_elections` reads it, and that pair is the whole raise. It buys two
    # facts that had no honest home: the crown, which rode `round:complete` and so was served a
    # whole `l1_critique` call after `elect_round_winner` decided it, and `matched_origin_*`,
    # which is stamped AFTER `candidate_scored` and therefore read null on 481 of 481 candidate
    # snapshots. θ deliberately does NOT move with them — it is RESTAMPED when the ruler warms,
    # which is what round 0's second close exists to deliver, so it stays on the record that
    # replays. What this unblocks is a deletion: with both served, the browser's per-bar
    # live-vs-tree choice collapses to "the tree, unless it has no measurement yet".
    # 4117 -> 4118 (2026-08-11): the CLOSE record moves onto the writer seam it always belonged
    # to. `RunCallbacks.on_round_close` replaces the raw `PhaseRecord` `persist_round` was
    # building inline, so `_rebank_on_branch` can bank a repaired round the same way a live round
    # is banked instead of spelling the payload a second time. Net one param: the method costs
    # two, `persist_round` gains `cb` and loses `round_num` (which was always `round_result.round`
    # — two spellings of one coordinate, and the record now reads the one on the result).
    # 4118 -> 4119 (2026-08-11), net of +2 and -1.
    # +2: `se_key` on `cell_measurand` + `compute_outer_verdict`. The SE beside an L4 measurand
    # names a DIFFERENT quantity from the measurand — the arm's own half of a paired difference,
    # shared origin excluded — so its key cannot be derived as `f"{measurand_key}_se"`. That
    # derivation was the assertion that made the origin's error get counted twice, which drove the
    # claimed noise above the spread it belongs to. Folding the two into one tuple would hold the
    # count flat and hide which key is which.
    # -1: `session` on `_calibrate_delta_ruler`, declared and never referenced, threaded through
    # three call sites. Deleting it leaves the function pure over its inputs, which is what let the
    # ≥2-arm identifiability rule finally be pinned by a test.
    "param_decls": 4119,
    # 4 -> 7 -> 4 (2026-08-11, same day, and the round trip IS the lesson). The three were
    # made lax mid-arc under a real rule — a model read back out of an append-only ledger or a
    # projection FILE punishes a removed field SILENTLY (`scan_ledger_round_closes` skips the
    # record, `resolve_resume_state` does not catch at all). What changed is not the mechanism
    # but the POLICY behind it: cycle state is now disposable, wiped rather than migrated, so
    # tolerating a stale key buys nothing and costs the loud failure that says "wipe". Strict
    # fails at the read with the field name; lax deleted a round's crown and said nothing.
    # The 4 survivors are structural, never historical, and each says so on itself: the backend
    # owns `PipelineNode`'s sub-vocabulary; `RoundResult` round-trips its own computed fields;
    # `Sample` carries whatever columns the operator's CSV had; `ScoredCandidate` guards a PAID
    # measurement, which `restamp.py::_SURFACES` names as deliberately outside the table.
    "models_lax": 4,
    # New (2026-08-06). Docstrings were 19.6% of the package's lines — 13282 of them against
    # 43418 lines of actual code — while `conventions.md` § Code style had carried a length
    # budget all along and 61% overran it. That drift happened under a WRITTEN rule with no
    # counter behind it, which is the whole argument for the dimension: a prose rule got
    # ignored for the same reason `<surface-ledger>` exists. Length is a quality tax on every
    # read, human or agent (`<simplify-the-problem>`), and nothing else here can see it — a
    # docstring adds no module, no field and no param, so the other fifteen rows read flat
    # while the package carried a third of a codebase in prose.
    #
    # It counts the LINES, and it did not start there. The first pass counted every docstring;
    # over a batch that deleted 791 lines the population moved 13, so it was narrowed to the
    # ones over the ≤2-line budget. Both are blind, in opposite directions: a population count
    # cannot see a 52-line essay, and an over-budget count cannot see what a compression sweep
    # actually accumulates, which is COMPLIANT docstrings. A third of the way in, that blind
    # spot already held 577 one-liners and 462 sitting exactly at the budget — a thousand
    # survivors the row could not price, all of them still prose a reader pays for. Lines see
    # every move: one deleted, one compressed, and an essay growing back at its own length.
    #
    # 1033 over-budget -> 9774 lines (2026-08-06): a counter-DEFINITION change. The two are not
    # comparable and neither is a TOTAL across them; the sweep's own history is in `git log -p`.
    #
    # A debt counter DESCENDING to a floor, unlike its neighbours which already sit at one, and
    # the floor is FAR below where this stands. It is the set a generator reads, which makes it
    # product rather than documentation — the `EXPORTED_MODELS` docstring FIRST lines
    # (`scripts/build_ts_types.py` reads `splitlines()[0]` into
    # `webapp/lib/api/types.generated.ts`), the FastAPI route bodies, and the 32 Pydantic/enum
    # component descriptions the docs UI serves out of `docs/specs/openapi.generated.json` —
    # plus the survivor of the two gates, rare by construction because anything binding a SET
    # of symbols belongs to a layer `CLAUDE.md`. That set is pinned when the sweep reaches it;
    # until then every drop here is ordinary prose deleted, and the number only falls.
    #
    # 3386 -> 3389 (2026-08-07): two-gate survivors on the headline-lift fix — `_ability_delta`
    # (why LATEST and not max) and one line on `_field_value` (the splice takes an EMPTY middle).
    # Both state a rule the code's shape cannot, and the long form lives at the `ability_delta`
    # field note rather than being repeated at each reader.
    #
    # 3389 -> 3391 (2026-08-07): `committed_task_context`, a two-gate survivor â€” it states WHY the
    # pure reader exists (a decomposition needs a cycle to bill to, and a mint is computing that
    # cycle), which no reading of its body recovers.
    #
    # 3391 -> 3410 (2026-08-07): sample look-ahead. Each survivor states the one fact no
    # signature carries and no layer CLAUDE.md owns — a throughput toggle cannot reach a
    # measurement — and its violation is silent.
    #
    # 3410 -> 3440 (2026-08-07): the served hard-sample rank. Two-gate survivors only, and
    # each states a rule its signature cannot: *measured* is dot presence in THIS scope and
    # not a δ entry (the ruler inherits from parent fits, so δ overcounts); SELECTION and RANK
    # are deliberately different keys, because the series is fetched only for rows already
    # selected; the `log.md` GRID stays δ-sorted whatever the knob says, since a Rasch matrix
    # reads as a staircase only while difficulty runs monotonically across its columns. Every
    # one of those is silent when violated — the ordering simply comes out wrong.
    #
    # 3440 -> 3452 (2026-08-08): six two-line survivors from the ledger-payload pass, each
    # stating a hazard its signature cannot and each silent when violated. `ledger_sample_view`
    # — NEW dicts, never a pop, because the argument is the same object the archive row, the
    # round file and `trajectory_results` all hold. `_view_fields` — a replay hands back a dict
    # where the live path holds the frozen view. `ViewContext.ledger_anchors` — why not `asdict`
    # (it re-emitted both whole prompts per candidate). `on_sample_started` — the query preview
    # is capped at the WRITER now, so the reader's slice is gone and cannot drift from it.
    # `_compact_one` — tmp + `os.replace`, because `append` is not crash-atomic and a torn
    # rewrite loses the cycle. `_compact_record` — what "already current" means for a stored row.
    #
    # 3452 -> 3459 (2026-08-08): `flush_pending_decisions` (5) + the test it guards (2). The
    # function's says WHY a second flush site exists at all — a decision recorded after the
    # last round persisted has no next `persist_round`, and dies in memory. Nothing about the
    # signature suggests a teardown seam, and the failure is a silently re-spent budget.
    #
    # 3459 -> 3471 (2026-08-08): the re-file pass (12). `_document_decisions` states the one
    # thing its body cannot: it keys on the ROUND STAMP and never on ledger position, because
    # round 0 closes twice and the next close after a round-1 decision reads 0 — trusting
    # position dropped 118 replayed decisions in the dry run. `refile_round_decisions` states
    # what makes a foreign entry harmful rather than untidy: `replay_decisions` re-derives it
    # against the wrong round's measurements, and resume halts or fails to.
    #
    # 3471 -> 3549 (2026-08-08): the eight-module split above (78). Every new module's
    # docstring answers the question its existence raises — *why is this not in the file it
    # came from* — with the defect that forced it, because a reader who cannot see that will
    # put the next function back. `l1_wire_schema` names its twin `build_l1_response_model`
    # and why a disagreement between them fails every parse; `selection` records that
    # `composite_ci` may not travel with the fitness gateway because `_mean_fitness_by_cell`
    # must stay un-predicated; `behavior_base` names the verbatim `CheckFn` duplication it
    # ended.
    #
    # 3549 -> 3584 (2026-08-08): the six modules above (35). Same rule as the raise before it —
    # each says why it is not in the file it came from. `repair.py` records that the cut must be
    # read off the round documents BEFORE the correction, since the correction plugs the holes
    # it is read from; `dashboard_rows.py` records why it is not called `round_summary`.
    #
    # 3584 -> 3627 (2026-08-08): `CurrentRound`, `DashboardCandidate`, `RoundSummaryCandidate`,
    # `LedgerAbility` and the projection seams that fill them. Paid deliberately, because the
    # raise is ON A SCHEMA and the field notes ARE the contract a reader needs to not
    # re-introduce the bug: `CurrentRound` records that there is no `live` flag beside `round`
    # and why re-deriving one as "has this round closed" blanks the canvas through the whole
    # post-round escalation window. The first draft of this arc was 3648 — the difference is
    # provenance prose that belonged in the commit body, and the same two stories written out
    # ten times each across the diff. One anchor per rule, a pointer everywhere else.
    #
    # 3627 -> 3637 (2026-08-11): two docstrings from the whisker/crown collapse, against
    # `theta_accuracy_ci`'s which went with the function. `mark_winner` carries the rule whose
    # absence produced the bug — the election is the END OF SCORING, so a crown published at
    # round close surfaces whenever an unrelated node happens to finish. `LedgerAbility` carries
    # the one that nearly caused a worse one: dropping a field from a STRICT ledger model
    # deletes history silently, because the scan skips a record it cannot validate.
    # `DashboardCandidate` carries the same rule for the projection half — its reader does not
    # catch, so the same deletion made every older cycle unresumable.
    # `view_fields`'s own docstring landed here with the move, and it is the rule a `getattr`
    # on a replayed record silently breaks.
    #
    # 3653 -> 3671 (2026-08-11): the whisker restamp (18). Two-gate survivors only.
    # `restamp_candidate_bands` states what the walk cannot: the band had TWO writers, so the
    # ledger snapshot and the round document hold different numbers for one candidate on 204 of
    # 448 coordinates, and nothing reconciles them — the tree reads one, a drill-in reads the
    # other. It also states why `scoreboard` is rewritten beside `candidate_scores` (same pair,
    # re-served through `_SCOREBOARD_INCLUDE`; leaving it moves the disagreement one key over)
    # and why an unmatched row is REPORTED rather than nulled. `_ledger_bands` names the one
    # writer. `_iter_cycle_ledgers` records the depth-limited glob that made an earlier census
    # read 25 round files where the store holds 218 — the reason the walk is written once.
    #
    # 3671 -> 3685 (2026-08-11): the mask's spine fold (14). Two-gate survivors: `SpineCycle`
    # states why a second, smaller shape exists beside `MaskCycle` (the rewind reads only
    # round-CLOSE facts, so it opens no round document); `_mask_candidate` states that the crown
    # is READ from the ledger and joined on `label`, never re-derived from the document's
    # `scoreboard[]` on `candidate_id` — the key the tree refuses because a resume re-mints it;
    # `_mask_records` states the identity bug it fixes, which no reading of a dict key recovers
    # (an inner campaign id is content-addressed on the CELL, so one id lives in several
    # sandboxes and the first visited won — 7 of 40 here, 17 records served the wrong numbers).
    #
    # 3685 -> 3696 (2026-08-11): the two verdict predicates (11). Both are CORRECTIONS of prose
    # that was false, which is the costliest kind: `make_scoring_verdict` claimed feeding the
    # realizing criterion "reproduces is_winner exactly" while ranking on a point estimate the
    # election does not use, and `_mask_eligible` claimed to carry the recorded election filter
    # while asking a weaker question. A reader who believes either stops looking.
    #
    # 3696 -> 3681 (2026-08-11): the band restamp's prose went with the verb it documented (15).
    #
    # 3681 -> 3669 (2026-08-11): the three lax-model justifications (12), deleted with the
    # laxness they argued for. Each cited a count of files that no longer exist — the exact
    # shape that turns a docstring into a false bug report.
    #
    # 3669 -> 3678 (2026-08-11): decisions' one home (9). `scan_ledger_decisions` carries the
    # rule its body cannot — it keys on the round STAMP and never on ledger position, because
    # round 0 closes twice and the next close after a round-1 decision reads 0, which misfiled
    # 118 records. `_copy_parent_decisions` states why a fork copies them at all, and why only
    # decisions: the rest of the prefix is the parent's own history, and a ledger that already
    # measured 56% duplication does not need it twice.
    #
    # 3678 -> 3693 (2026-08-11): the election record (15). Every survivor states the SPLIT and why
    # it is not arbitrary — the crown is stamped ONCE and goes to the election; θ is RESTAMPED when
    # the ruler warms and stays on the close, which is the whole reason round 0 closes twice. Move
    # either half to the other record and nothing raises: round 0 quietly reverts to its cold θ, or
    # a served crown goes late again by an `l1_critique` call. `scan_ledger_elections` carries the
    # one its signature cannot — an absent entry means the round never elected, a different fact
    # from a round that elected and held. The record carries the crown and NOTHING else: the rest
    # of what the election stamps is per-candidate and already addressable in the round document,
    # so by this package's own test it earns no chronology.
    #
    # 3693 -> 3697 (2026-08-11): a repaired round enters through the whole ingress (4).
    # `on_round_close` states the half of the split that lives on it — every term RE-READ per
    # close, which is what lets round 0's second one carry the warm ruler's θ — and
    # `_rebank_on_branch` now says that a round banked without its close is a round every ledger
    # scan goes blind to. The rewind-admissibility sentence was cut from both: that rule is
    # `infrastructure/CLAUDE.md`'s, and a second copy is what drifts.
    "docstring_lines": 3697,
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
