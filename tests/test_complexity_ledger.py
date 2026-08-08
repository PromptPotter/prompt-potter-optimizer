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
    "modules": 302,
    "init_files": 47,
    # A FLOOR: none of the 5 is a shim, and emptying any of them breaks the app. `connectors`
    # and `presentation/api/routers/campaigns` ARE registries — the submodule imports run the
    # `@campaigns_router` decorators, so emptying the latter mounts ZERO routes. `shared`,
    # `application/scoring/formula` and `application/views/render` hold real code in the body,
    # which a text test cannot see.
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
    "any_params": 65,
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
    "domain_any_maps": 76,
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
    "param_decls": 4089,
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
    "docstring_lines": 3440,
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
