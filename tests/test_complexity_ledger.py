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
    # 65 -> 66 (2026-08-06): `_keep_known_keys._v`, whose `Any` is the same one its peer
    # `_truncate._v` carries — a Pydantic `BeforeValidator` runs before coercion, so the
    # value it inspects is genuinely untyped. Typing it narrower would be a claim the parse
    # boundary cannot make.
    "any_params": 66,
    # New (2026-08-05). Lowering it is what makes the cycle-index modelling question
    # (`docs/specs/code-debt-cleanup.md`) falsifiable rather than a judgment call.
    # 73 -> 74 (2026-08-06): `modal_answer_share`. A deliberate raise for the CONTINUOUS
    # reading of a question the package could only ask as a bool — `is_answer_collapsed`
    # needs a literal single label, so a model answering one label 95% of the time passed
    # every check and graded `healthy`. It reports and never gates: hedging is the failure
    # the loop corrects, so the number's value is its round-over-round series.
    "domain_any_maps": 74,
    # Moves on 2026-08-06 collapsed to their standing state; `git log -p` is the history
    # layer, and a running tally here is the sweep-log shape the backlog doc names as the old
    # bloat source. What the current number buys, newest first:
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
    # aggregates), `cell_readings` + `_cell_reading_lists` + `cells_dropped` (split a cell's
    # readings from their mean; make a dropped cell loud), `model_cls` on `restamp._process`
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
    "param_decls": 4080,
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
