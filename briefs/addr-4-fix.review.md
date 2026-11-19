# Review: arc/addr-4 (e8183708f..383c97118), 8 commits

Verdict (whole tip): **land after fixes.** R1 and R3 are worse than the base and block the landing.
R2 is no regression (the base reads untyped nulls the same way): it is link 1's root left
unfinished, and whether it needs another build is the orchestrator's reading of the brief.
Offline equals-main reproduces clean: no decision moves; the L4 leg moves only by link 3's nonce
(inner requests equal under a nonce mask) and link 2's `inner_origin` leaving the outer params.

Probes ran read-only on `git archive` copies of the base (`scratchpad/a4base`) and the tip
(`scratchpad/a4tip`), repo venv, `PYTHONPATH` pinned to each copy. Scripts: `scratchpad/a4probe/`.

---

## R1 (HIGH, link 2 40e48d60a): a moved cell identity no longer refuses a resume

**Where:** `promptpotter/application/runner/mint.py:64-72` (`origin_content_hash` hashes
`points.job(origin).content_hash` = `content_hash(dataset, pipeline_params.dump())`);
`promptpotter/application/jobs/mint.py:370` (`resolve_resume`: `root_content_hash == checked.origin_hash`).

**Defect:** at the base the identity layer (judge fingerprint, harbor agent fingerprint, n8n
base-url hash, L4 inner estimator) was folded into every point's `pipeline_params`, so it reached
the origin hash, and the origin hash was the ONE thing that noticed a moved identity. Link 2 takes
identity out of the params (right, per the brief) but moves no replacement into the drift guard.
- `runner/mint.py:71` still claims "Config-AWARE, as the measurement key is: a connector-config edit
  yields a DISTINCT origin": false now (the archive key holds identity, the origin hash does not).
- The same hash gates a head-to-head's bench set (`jobs/mint.py::_refuse_off_record`, `record.origin
  != plan.origin_hash`) and `OriginAlreadyRun` (`runner/mint.py:295`): an arm minted under another
  harbor agent, n8n URL, inner estimator or judge fingerprint (a moved rubric under one `JudgeSpec`)
  now joins the bench set unrefused (a different `JudgeSpec` is still caught: `SharedPart.environment`
  carries the judges).
- `PartitionRecord.instrument_id` (`instrument_of`) holds only `dataset_hash@inner_origin`, so the
  judge/harness identity a cycle ran under is recorded nowhere but the archive's config files. The
  brief's "`ParamLayers.recorded` keeps the identity a measured point ran under" is deleted, not
  moved; nothing took over its job.
- After such a resume the live `Session.instrument_id` (`runner/round.py:429,768`) and the drawn one
  (`evidence/read.py:814`, `runner/bench.py:886`) can disagree (L4 estimator move).

**Failure (reproduced):** `scratchpad/a4probe/test_probe_identity_drift.py` (uses the `held`
fixture of `tests/test_identity.py`; monkeypatches `pipeline_resolve.identity_layer` to add
`solve.judge_instrument` at resume):
- base: `RESUME REFUSED: campaign decl__000001 no longer resolves to the origin it was minted on (stored 028e87aeda26, now 73ba9cec0c92)`
- tip: `RESUME PASSED under a moved identity`, `cell_identity: {'solve': {'judge_instrument': 'a-moved-instrument'}}`.

No test covers it: `git grep -n ConfigDriftError -- tests` hits only bank/answer-term/capability drift.

**Fix:** the cycle files the whole cell identity it drew under (widen `instrument_of` to hash
`cell_identity`, or a `PartitionRecord.cell_identity`), and `resolve_resume` / `_refuse_off_record`
refuse a moved one naming it (this is the adjacent ledger row "A moved capability or backend
revision alone passes a resume", widened). Keep identity out of the origin hash, which names the
root id the brief lets move; whether a draw is seeded off it is traced, not run.

**Falsify:** copy the probe into `tests/` of a tip copy, run
`python -m pytest tests/test_probe_identity_drift.py -s` (prints `OUTCOME passed`; `refused` at base).

**Confidence:** high (reproduced both sides).

## R2 (HIGH, link 1 aea743e0a; not a regression): null still does not mean CLEAR on every layer; F1 still reproduces on untyped leaves (three call keys tested); three knobs were only retyped

**Where:** `promptpotter/domain/layer_fold.py:121-152` (`_laid` lays a raw `None` as a value, or,
under `Lay.SUBSET`, keeps the fold's list); only `wire_patch` (`:205-217`) turns null into `CLEAR`.
Raw routes unchanged: `datasets/authored.py::ConfigLayers.folded/stating`, `_campaign_block`,
`embedded_run`, `teleprompter`, L4 `inner_campaign` (`LaidAs`).

**Defect:** the decided shape is "null means CLEAR on every layer". The build retyped
`TranscriptKnobs.transcripts/transcript_chars` and `EscalationKnobs.l3_patience` to
`int | "unbounded"`, which closes F1's silent round-trip for those three leaves only. A raw layer
still reads null three ways:
- **refused** where the type no longer admits it: a yaml/payload `transcripts: null` raises
  `PayloadInvalidError optimization.nodes refused by optimizer 'potter'` (`p1_null.py`), where a door's
  null clears to the manifest's 3;
- **kept** under SUBSET: raw `param_keys: null` over `[a, b]` lays `[a, b]`, a door's null lays `{}`
  (`p4_subset.py`: `raw null -> {'param_keys': ['a', 'b']} | door null -> {}`);
- **a value** on every other `X | None` leaf (`halt_at_fitness`, `fitness_ceiling`, `lives`, …).
- `knobs.py:18`'s comment ("``null`` clears on every layer (`layer_fold.py::wire_patch`)") is true
  only of door patches.

**F1 is still open on untyped leaves (reproduced, same at base):** `p7_untyped.py` lays a raw
`optimization.nodes.l1_critique.config.{temperature|max_tokens|reasoning_effort}: null` over the
defaults. `ConfigLayers.config()` (what the door validates and preflights) runs `None`; the
declaration's loop names the null; `effective_config(declare_run(cfg))` (what every mint runs)
runs the manifest's `0.3` / `16000` / `low`; `ROUNDTRIP False` on all three, base and tip alike.
The new round-trip case in `test_a_declaration_lays_back_what_it_dropped_from_the_defaults` covers
only `"unbounded"`, so it cannot see this.

**Second "no bound" spelling kept (Budget):** `domain/spend.py::Budget` ("a named null lifts"),
`campaign_config.py:151-163` ("A null axis is unbounded"), `LivesConfig` doc ("sets
`budget: {rounds: null}`") and `ConfigLayers.over` ("a null axis is unbounded, never a clear").
`budget` is a `Knob(Estimand.SPEND)` whose value needs null to mean unbounded: by the brief's letter
("a knob whose value today needs null to mean every / whole / unbounded gets an explicit non-null
spelling… one spelling for all of them") it is in scope and unmoved. It is also what stops the fold
itself from reading null as CLEAR (a raw `budget.rounds: null` would then clear to 5).

**Fix:** spell Budget's lift `unbounded` too (one `Unbounded` in `domain/`, not potter's
`knobs.py`), then make `_laid` read `None` as `CLEAR` (one rule in the fold) and delete
`wire_patch`'s null branch and `ConfigLayers.over`'s budget carve-out.

**Falsify:** `scratchpad/a4probe/p1_null.py`, `p4_subset.py`, `p7_untyped.py` on the tip; add a
raw-null `temperature` leaf to the round-trip test and it fails.

**Confidence:** high (reproduced). Budget-in-scope is a reading of the brief: the orchestrator's call.

## R3 (MEDIUM, link 1): a SURVIVOR install shadow of potter's manifest that loaded at the base refuses the landing, with no migration

**Where:** `infrastructure/store/data_root.py:257` (`OPTIMIZER_SHADOW`, `optimizers/*/pipeline.yaml`,
`Fate.SURVIVOR`); `maintenance/landing.py::_shadow_why` -> `check_treatment` -> `select_optimizer`
validates the knobs; `config/paths.py::optimizer_manifest_path` (a shadow replaces the shipped file
whole).

**Failure (reproduced):** `p5_shadow.py` writes the shipped potter manifest with `l3_patience: null`
as an install shadow: base `SHADOW (l3_patience: null) LOADS`; tip `SHADOW REFUSED: PayloadInvalidError
… EscalationKnobs l3_patience … Input should be a valid integer`. Same for `transcripts`/
`transcript_chars`. The SURVIVOR adapter schemas themselves are unchanged (`p6_survivors.py`: all 16
SURVIVOR kinds hash identically base/tip, so no job file holds a `PartitionRecord`); the break is in
the knob validation the landing runs on the shadow.
- Side effect of link 3 on the same kind (traced, not run): a shadow copied from the base manifest
  (`k_max: 5`) used to be overridden by `defaults.yaml`'s `nodes` 0; at the tip nothing overrides it,
  so a default campaign on that install runs `k_max: 5` and re-badges `shots_without_demo_pool`.

**Fix:** a one-shot landing migration rewrites `null` -> `unbounded` on the three knobs in an install
shadow (§ 0011; `landing.py` is bkeys-4's, so a row to file), or the operator confirms the box holds
no `optimizers/*/pipeline.yaml` shadow (`ls $PROMPTPOTTER_HOME/optimizers/*/pipeline.yaml` on the box).

**Confidence:** high that it refuses; whether any install holds such a shadow is unknown.

## R4 (MEDIUM, link 2): the archive's address is identity-free while its file key is identity-keyed

**Where:** `promptpotter/application/datasets/loaders.py:486-493` (`config_key` from
`params.filed(cell_identity)`, `prompt_fields_id = sp_hash` of the unfiled params).

**Defect:** at the base `prompt_fields_id` and `config_key` were 1:1 (both hashed the identity). Now
one `prompt_fields_id` (`ArchiveConfig.individual`, "the archive address") spans several
`config_key`s, one per identity. Readers that index by it collapse identities:
- `application/bench/difficulty.py:175` `entries = {entry.prompt_fields_id: entry ...}` keeps one
  entry per address, then `archive.measured(entry, keys)` reads THAT identity's cells for a ruler row
  filed by another;
- `infrastructure/store/archive_queries.py:111` `bench_reads` counts one holdout read where two
  instruments read it;
- ruler rows are addressed by `sp_hash` (`difficulty.py:197,263`); `RulerScope(scorer, dataset)`
  separates judges (the scorer id carries `judge_instrument`) but nothing separates a harbor agent,
  an n8n URL or an L4 inner estimator on one dataset name (traced, not run).

**Failure (reproduced for the index):** `p2_collide.py`: one point under `judge_instrument` A and B ->
`config_key` differ, `prompt_fields_id` equal, the `regraded_ruler`-style index keeps 1 of 2.

**Fix:** every reader that addresses the archive keys on `config_key` (`regraded_ruler`'s index,
`bench_reads`' count), and a ruler row's address carries the identity too (the ruler files rows by
the arm's `sp_hash`, so filing `prompt_fields_id` alone would make `entries.get(address)` miss every
entry). The split follows from the brief's own decided shape (an arm's id is what it sends), so
which address the ruler keeps is the orchestrator's call.

**Falsify:** `scratchpad/a4probe/p2_collide.py` on the tip.

**Confidence:** high for the collapse; medium for the ruler mixing (traced).

## R5 (MEDIUM-LOW, link 1, entry-point parity): the webapp cannot show or offer `unbounded`, and the CLI takes it only JSON-quoted

- **REST / webapp knob row:** `domain/pipeline_schema.py:723-750` (`_knob_row`) takes the first
  non-null `anyOf` arm, so `int | "unbounded"` renders as a plain integer, `options: []`,
  `nullable: False` (`p9_knobrow.py`; base: `nullable: True`). The Optimizer card cannot show or
  offer the value promptpotter-self sets.
- **CLI:** `--set …l1_critique.config.transcripts=unbounded` is refused (`'unbounded' is no JSON
  value`); only `'"unbounded"'` passes (`p3_cli.py`), because `nodes.<n>.config` is `dict[str, Any]`.
- **Dashboard limits:** `potter/members.py:571-577` maps `"unbounded"` back to `OptimizerLimit.value
  = None`, which `dashboard_rows.py:197` documents as "declared off" and `LimitReconcile.tsx` shows as
  the placeholder "inherit": a third spelling of no-bound on the wire.

**Fix:** `_knob_row` reads every arm (an integer with a word option), and `OptimizerLimit.value`
carries `Unbounded` rather than None.

**Falsify:** `p9_knobrow.py`, `p3_cli.py`.

**Confidence:** high.

## R6 (LOW, link 4 7c716a647): the purpose is passed twice, and a declaration still passes as a spec

**Where:** every caller pairs `run_spec(session, config, P)` with `resolve_cycle_plan(session, spec,
P, …)`: `jobs/launcher/checkin.py:95-98` (`optimize := Optimize()`), `jobs/mint.py:512-513`,
`runner/inner/spawn.py:275-276`, `tests/factories.py:721`; `mint_framed_cycle` (`jobs/mint.py:692`)
hard-codes `Optimize()` while its callers built the spec with their own `Optimize()`
(`embedded_run.py:173`, `mint_and_start.py:111`). `resolve_resume` passes the stored
`RunDeclaration` as the `spec` (`jobs/mint.py:364,369`), which is why `RunDeclaration.of` now filters
to `RunSpec.model_fields` (`domain/campaign.py:209-210`).
**Defect:** nothing ties the spec's compare narrowing to the plan's purpose: an Optimize-built spec
under an `ArmSeat` (or the reverse) plans silently. `RunDeclaration(RunSpec)` inheritance is the root
of the "a declaration passes as a spec" filter.
**Fix:** `resolve_cycle_plan` takes the config and the purpose and calls `run_spec` itself (resume
passes its declaration's spec part), so the purpose is named once; `RunDeclaration` holds a `spec`
rather than inheriting.
**Falsify:** `git grep -n "run_spec(" -- promptpotter` shows each call beside a second spelling of its
purpose. **Confidence:** medium (shape, not a reproduced failure). Numbering itself is once:
`_numbered` has one caller (`_mint_all`, `jobs/mint.py:634`).

## R7 (LOW, link 5 7f806f600): correct, but no test pins either normalization

`p8_hash.py`: tip keys effort `"default"` = none (`3666ef65b1` both) and route `[]` = none; base kept
them apart (`0bca5c8a0c` / `c058c8ee83` vs `3666ef65b1`). `git grep -n hash_call -- tests` shows no
case for either. Fix: one test asserting both pairs key alike. Smaller point: effort is normalized at
hash and send time (`sent_effort`, imported into the store from `openai_compat`) while route is
normalized once in `ChatRequest.__post_init__`; normalizing `"default"` there too would make both
levers one mechanism and drop the store's import of a client module.

## R8 (LOW, link 7 8814c8818): `display_metric` is display-only, but two code defaults still say `fitness`

Every reader (`git grep -n display_metric -- promptpotter`): `scoring_scope.py:60`,
`run_observers.py:89`, `readout.py:146/283`, `ansi.py:225`, `live_dashboard.py:365`, `couplings.py:49`
(an inert-coupling badge; `config_map.py:96` treats `Estimand.DISPLAY` as unmeasured;
`measured_address` excludes it), webapp `ForestCard.tsx`/`useCandidatesModel.ts`. None reaches an
election, cut or race order, so no DECISION moves.
- `presentation/teleprompter.py:126` hard-codes `"display_metric": "fitness"`, so the DSPy door
  still raises `display_subset_relative_under_resubset` where its `loop` runs potter with a per-round
  resubset (link 7's symptom, one door left; traced, not run).
- `views/readout.py:146` defaults `_display_metric = "fitness"` before the wiring folds: a second
  spelling of the default in code.
- Smell in passing: `ScorerSetup.display_metric` (`initialization/session.py:44`) has no reader.
Fix: drop the teleprompter's literal (it takes `defaults.yaml`'s) and the readout's fallback.

## R9 (LOW, link 2): `PipelineParams.filed` collides with two other `filed`s

`domain/pipeline_params.py:183` beside `domain/scoring.py:527` (`…filed(facts) -> GradedCell`) and
`infrastructure/llm/spend_book.py:639` (`filed(label)`), in a repo where "filed" is the archive's
word for an answer (`FiledAnswer`). Fix: name it for what it lays (e.g. `keyed_by(identity)`).
Also `test_the_judge_a_cell_is_graded_by_moves_its_archive_key_and_no_arm_id` never asserts the arm
id is unmoved under two judges (it holds `sent` fixed by construction).

## R10 (INFO, link 1): a backend extra's null is now dropped

`NodeCall._keeps_order` drops a null backend extra. In-tree, only
`connectors/resources/websearch-entity-profiling.n8n.json` documents a null param
("profiling_max_tokens: null (no limit)"), and its code (`body.profiling_max_tokens ?? 1800`) reads
null as absent, so no request moves. TermNorm's served `GET /pipeline` config is outside the tree:
falsify by checking a live served config for a null-valued param.

## Checked and correct

- Link 2: `git grep -c _without_identity -- promptpotter` = 0; every archive writer/reader of a
  point's cells keys `params.filed(cell_identity)` (`loaders.py:490`, `passes.py:300`,
  `search_point_scorer.py:298`, `Session.instrument_id`); `measured_cell_price` matches on models
  alone (identity-agnostic by design). The reply cache keys the request as sent, so two arms sending
  one request under two identities share a reply, which is right; their archive cells stay apart.
- `ParamLayers.recorded` deletion is safe for its one reader (`resolve_pipeline_for_campaign`):
  `params` and a round's `resolved_params` now both carry no identity. What `recorded` also stood for
  (the identity a cycle ran under) is R1. `PartitionRecord.instrument_id` (now required) is RUN data;
  no SURVIVOR kind's schema moved (`p6_survivors.py`).
- `fork_siblings.py:257` no longer lays a candidate's identity keys into a fork's `origin_params`
  (a base-side leak link 2 fixes).
- Link 3: `k_max: 0` in `optimizers/potter/pipeline.yaml:73`, the `defaults.yaml` `nodes` entry and
  the test filter gone, `inner_campaign` names `k_max: 0`; no other manifest borrows potter's
  `l1_generate` (`capo-pobb` borrows only `pobb`).
- Link 6: both refusals tested both ways (`test_a_declaration_refuses_a_subset_list_that_widens`,
  `…_union_list_that_is_not_the_defaults_plus_extras`); `_list_laid` is the one list rule.
- Link 7: `patch_between`'s CALL `route_order` re-add is gone; `_laid`'s (the fold's live one) stays.
  `Environment.target`'s docstring is updated.
- Lint: `ruff check` / `ruff format --check` clean on the touched files.
- Lines: `promptpotter/` +224/-247 at the tip; per link +40/-27, +79/-123, +3/-6, +71/-79, +10/-5,
  +23/-3, +4/-10; `package_lines` 112256 -> 112236.

## Collisions with arc/bkeys-4

Beyond the generated specs, `behaviour_keys.json` and `complexity_ledger.py`, the one file both touch
is `tests/test_security.py`, in disjoint hunks (addr-4: the `factories`/`jobs.mint` import blocks and
one test at :3859; bkeys-4: `inspect`/`kinds`/`data_root` imports and the landing-key test at :2945).
No bkeys-4 file calls a signature addr-4 changed (`PartitionRecord.of`, `archive_entry`,
`resolve_cycle_plan`, `declare`, `cycle_instrument`).

## Equals-main (reproduced here, offline, zero spend)

`PROMPTPOTTER_HOME=scratchpad/hb python scripts/offline_run.py` on the base copy (397 s), then
`PROMPTPOTTER_HOME=scratchpad/ha python scripts/offline_run.py --against scratchpad/hb` on the tip copy
(504 s); logs `a4probe/offline_base.log`, `a4probe/offline_tip.log`; `behaviour_keys.json` unchanged
against each tree's own committed keys (so the tip's committed `estimator 78e1d9f1` is what the tip
computes).
- capo, capo-pobb, gepa, levi, potter and every potter variant leg (crossed, crossed-l2,
  crossed-resumed, healed, resumed, wounded, diverged): **UNMOVED**.
- potter/l4: MOVED `decisions.inner[0..5]`, `decisions.rounds[0..2]`, `run.result_pipeline_params`,
  `inner_requests` (48 of 24). Leaf diff (`a4probe/p10_l4diff.py hb/potter/l4/trace.json
  ha/potter/l4/trace.json`, all 32 leaves read): every inner leaf is the fake's quoted call nonce
  (`critique 2ac07939.2` -> `critique ec13b818.2`: link 3's `inner_campaign` `k_max: 0` re-keys the
  overlay-keyed nonce); every outer leaf, `run.result_pipeline_params` included, is
  `resolved_params.l1_generate.inner_origin` leaving (link 2). No election, pick or cut leaf moved.
- `inner_requests` "48 of 24" is the union of keys (`BehaviourTrace.moved`): 24 request files on each
  side, named by the nonce, none shared; under a nonce mask the name sets are equal and all 29 bodies
  are equal (`a4probe/p11_inner_req.py`, `a4probe/p12_bodies.py hb/potter/l4/requests
  ha/potter/l4/requests`: `bodies equal under nonce mask: 13 differ: 0`). The requests move by the
  nonce alone; the builder's attribution holds.
- This covers neither R1 nor R4: the offline world moves no identity between mint and resume, and
  holds one identity per dataset.
