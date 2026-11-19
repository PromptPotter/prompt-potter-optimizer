# Review: arc/addr-3 (d6ca65e3d..b06e3be2d), 4 commits for 5 rows

Verdict: **land after fixes.** F1 is a silent config change: the run executes knobs other than the ones its root id addresses. F2 is a missing link. Everything else is low, or a call for the operator.

Probes ran read-only on `git archive` copies of the base and the tip, in `scratchpad/r3base` and `scratchpad/r3tip`. They used the repo venv with `PYTHONPATH` pinned to each copy (scripts in `scratchpad/r3probe/`). No gate, no `offline_run`, no paid call.

---

## F1 (HIGH, link 1 c964f4cfc): a `null` knob value is dropped between the declaration and the run

**Where:** `promptpotter/domain/layer_fold.py:186-187` (`patch_between` emits `None` for a leaf `over` holds as null) and `promptpotter/application/datasets/authored.py:143` (`effective_config` lays the patch through `wire_patch`, which reads that `None` as `CLEAR`).

**Defect:** a literal `null` is a meaningful value in an untyped `dict[str, Any]` leaf, so the round trip is broken there. Two such leaves:
- a node's `optimization.nodes.<n>.config`: `potter/knobs.py::TranscriptKnobs.transcripts` / `transcript_chars` document "``null`` shows every one / each whole";
- `NodeCall` extras in `pipeline_overlay`.

`patch_between` spells "this leaf is null" and "drop this key" the same way. The fold then pops the key, and at selection the manifest's value shows through.

**Failure:** `probe1.py` uses a defaults config plus `optimization.nodes.l1_critique.config = {transcripts: null, transcript_chars: null}`.
- At the base, `effective_config(declare_run(cfg))` returns the null knobs (`ROUNDTRIP True`).
- At the tip, `spec.loop` still holds them, so the root id addresses the null, but the effective config's nodes lose `l1_critique` (`ROUNDTRIP False`). The campaign then runs the manifest's `transcripts: 3` / `transcript_chars: 2200`.

**Reach:** every route that lays a layer raw rather than through `wire_patch`:
- dataset and family yamls (`authored.py::_campaign_block`);
- a launch payload's `campaign_config` (`commands/launching.py:414` → `ConfigLayers.of` → `stating`, laid raw by `folded()`);
- `embedded_run.py:172/249`;
- `presentation/teleprompter.py:47`;
- the L4 `inner_campaign` yaml (`LaidAs(CampaignConfig)`).

Door patches already read null as clear.

**Why the proof missed it:** no shipped yaml and no offline-world config holds a null knob, and the new test `test_a_declaration_lays_back_what_it_dropped_from_the_defaults` has no null leaf. So equals-main cannot see it.

**Fix:** give null one meaning on every layer and keep a declaration's null a value. Either `patch_between` emits a clear marker distinct from `null`, with `effective_config` laying the stored patch without `wire_patch`'s null→CLEAR, or the null-valued knobs get a non-null "whole" spelling so "null clears" holds everywhere. In both cases add a null-knob case to the new round-trip test.

**Falsify:** run `scratchpad/r3probe/probe1.py` against the tip.

**Confidence:** high (reproduced).

## F2 (HIGH, chain): row 3 "An identity re-key moves offline decisions" is unbuilt

**What is missing:** the brief orders five links, but the chain has four. The row is still in the ledger at the tip (`docs/specs/code-debt-cleanup.md:455`).

**Code still unchanged:** `pipeline_resolve.py::identity_layer` (l.432) still feeds `ParamLayers.identity`, which folds into the node params an arm's content id hashes (`pipeline_resolve.py:197`, `_layer` case `"identity"`). `pipeline_resolve.py` is in this chain's Owns.

**Failure:** the row stays open, so a later identity re-key still moves which cells are drawn and raced, outside W1.

**Fix:** build the link, an arm's id derived from what it sends. Alternatively the orchestrator confirms from the builder's report that it was deliberately deferred, and why.

**Falsify:**
- `git show origin/arc/addr-3:docs/specs/code-debt-cleanup.md | grep -n "identity re-key"` is non-empty;
- `git log --oneline d6ca65e3d..origin/arc/addr-3` lists four commits.

**Confidence:** high that it is missing. Whether it was a deliberate defer is unknown (report not read, per brief).

## F3 (MEDIUM, link 4 57b3a9e9b): the "unmoved" claim for the L4 leg is not shown. Inner requests move by construction

**Where:** `promptpotter/assets/behaviour_keys.json::estimator` moves (f252b1e3 → 0364c2e0). `scripts/offline_run.py::behaviour_keys` computes it as `stable_hash(inner_half())` = `[decisions["inner"], inner_requests]`.

**Why it moves:** the fake's nonce `scripts/offline_run.py::_asker` (l.388-400) hashes `[seed, the inner campaign's optimization.nodes overlay]`. Link 4 puts `l1_generate.config.k_max` into every potter overlay:
- `0` from `defaults.yaml`;
- `5` now explicit in `justlogic/family.yaml`, which the L4 inner seats on `justlogic/d234` inherit.

Every inner `call` nonce therefore moves. The fake replies quote it (`"critique {call}"`, `_variant(f"{node}.{call}")`), so the inner request text moves, and so do the L1 variants the fake writes.

**Status:** whether inner DECISIONS moved is not established by the diff. The commit body says "picks unmoved" but not "requests unmoved".

**Failure:** per the brief, a request move needs its row's attribution. A decision move fails the link.

**Fix:** report the L4 leg's equals-main explicitly (decisions unmoved; requests moved only through the overlay-keyed nonce, attributed to cfg-2).

**Falsify:** in a worktree, not the desk tree, because the run rewrites `behaviour_keys.json`: `PROMPTPOTTER_HOME=/tmp/ha python scripts/offline_run.py --against /tmp/hb`, reading the L4 inner lines. Decisions must read unmoved.

**Confidence:** high that requests move. Decisions unverified.

## F4 (MEDIUM, link 4): potter's `k_max` now has two defaults, and the 0 lives in an optimizer-scoped map

**Where:** `promptpotter/assets/datasets/defaults.yaml:18-21` lays `optimization.nodes.l1_generate.config.k_max: 0`, while `promptpotter/assets/optimizers/potter/pipeline.yaml:72` still says `k_max: 5`.

**Defect:** one knob now has two sources (the manifest and the campaign default), which is the "second source for one number" that `campaign_config.py::refuse_code_defaults` forbids for bench knobs. `nodes` is `ScopedBy("optimizer")`, so the 0 holds only while potter is the defaults' optimizer.

**Failure:** `probe2.py` lays defaults, then a layer naming `capo`, then one naming `potter`. The result has `nodes == {}` and `select_optimizer(...).knobs("l1_generate").k_max == 5`, so the dead axis returns.
- No shipped dataset takes that path (none names another optimizer). The `test_a_switch_drops_…` setup is that path, and the test edit had to filter `nodes` out.
- The `defaults.yaml` header ("each `CampaignConfig` field this file names is required there") is now false: `OptimizerChoice.nodes` has `default_factory=dict`.

**Ownership:** the brief fixed the location (operator 2026-11-19), so this is the operator's call, not a builder error.

**Fix (ask):** put `k_max: 0` in potter's manifest, the one home, and keep justlogic's `5`. That deletes the `defaults.yaml` `nodes` entry (+4) and the test filter, and keeps the header true. Otherwise keep it as decided and fix the header.

**Falsify:** run `scratchpad/r3probe/probe2.py` against the tip.

**Confidence:** high (reproduced). Severity depends on the operator.

## F5 (MEDIUM-LOW, link 4): the row's own case is left open on the L4 inner leg

**Where:** `research/datasets/promptpotter-self/family.yaml:141-149`. The `inner_campaign` holds `dataset_split.demo: 0` ("load-bearing") over `justlogic/d234`, which now explicitly names `k_max: 5`.

**Failure:** `config_map.py::_starves_shots` (`shots > 0 and demo == 0`) holds, so `l1_generate.shots_without_demo_pool` still badges every L4 inner cell on justlogic. The row says the axis "is dead wherever a dataset holds out no demo pool". Its re-test only checks default potter on shipped datasets.

**Fix:** add `k_max: 0` beside `n_variants: 3` in that `inner_campaign`. Link 4 already re-keys those cells (F3), so this costs no second void.

**Falsify:** build the inner config for a justlogic seat and evaluate the `_draw_couplings` predicate; it reads true.

**Confidence:** medium (traced, not run).

## F6 (LOW, link 5 b06e3be2d): one unset lever still has two spellings in the key, and `[]` changed meaning

**Set fields keep their key (verified):** in `probe6.py` with a schema set, an all-set request hashes identically at the base and the tip, with and without `route_order`/`top_p`.

**Two residues in `promptpotter/infrastructure/store/llm_reuse_cache.py:40`:**
- (a) `reasoning_effort="default"` and `None` send the same body: `openai_compat.py::sent_effort` omits "default". They still key apart (tip: b11e6ac7… vs b0a859c8…), so the "as sent" rule is unmet for the effort lever.
- (b) `route_order=[]` keyed as absent at the base (`if request.route_order:`) and keys as `[]` at the tip (64d60420… → 88b662e5…). It is unreachable today, because both builders normalize with `or None` (`bench/llm_call.py:358`, `connectors/harbor.py:1217`).

**Fix:** hash `sent_effort(request.reasoning_effort)`, and normalize an empty route once in `ChatRequest`, which deletes both `or None`.

**Falsify:** run `scratchpad/r3probe/probe6.py`.

**Confidence:** high (reproduced). Low impact.

## F7 (LOW, latent, link 1): `patch_between` cannot express every SUBSET/UNION list

**Where:** `promptpotter/domain/layer_fold.py:186-187` emits `over`'s list, and the fold intersects it (SUBSET) or appends to it (UNION).

**Failure:** `probe3.py` uses `under.param_keys=[a,b]` → `over [a,b,c]`, which lays `[a,b]`, and `prompt_fields_held [x,y]` → `[x]`, which lays `[x,y]`.
- Unreachable today: `defaults.yaml` holds no `optimizer_narrowing`, so `held` is never a list there.
- The reachable shapes pass: a narrowed `param_keys`, `param_keys: []` (what `_spec` sets for a compare run), and a grown `prompt_fields_held`.

**Fix:** `patch_between` raises on a SUBSET list that widens or a UNION list that is not `under` plus extras.

**Confidence:** high (reproduced). Latent.

## F8 (LOW, link 2 9405cb1c2): two shapes behind one field, and a caller-steered flag

**Where:** `jobs/mint.py:141-150` (`ArmSpec`, `spec_of`), `:157` (`declared: RunDeclaration | ArmSpec`), `:187-196` (`spec` property, plus a `declaration` property that asserts), and `:231-247` (`_spec(..., compare: bool)`).

**Defect:**
- The row's Action spelled a plan carrying a purpose-free spec plus `Optimize | ArmSeat`. The build puts a wrapper type and a free unwrapping function behind one field.
- `RunDeclaration(RunSpec)` inheritance means a `RunDeclaration` passes as a spec: `spec_of` returns the declaration itself. `RunDeclaration.of(plan.spec, …)` on a non-arm plan would then raise `TypeError` (duplicate `purpose`). Latent: `_numbered` only meets `ArmSpec` plans.
- `compare: bool` is a flag the caller steers. `_spec` could take the purpose or seat and derive it.

**Checked correct:**
- Numbering runs exactly once. `_numbered` has one caller (`_mint_all`); `_mint_all`'s three callers (`mint_arms`, `_mint_framed`, `_mint_after_framing`) each pass the plan set once; every arm path reaches it (`launcher/head_to_head.py::_mint`).
- An un-numbered arm asserts loudly at `cycle_id` and `declaration`.
- No placeholder remains: `git grep -n "replicate=1" -- promptpotter` is empty.
- Ids are unmoved (`stable_hash` sorts keys; the numbered declaration equals the old one).

**Fix (optional):** `CyclePlan.spec: RunSpec` plus `purpose: Purpose | ArmSeat`, which deletes `ArmSpec`, `spec_of` and the `spec` property. Not clearly net fewer lines across the six `resolve_cycle_plan` callers. Builder's judgement.

**Confidence:** medium.

## F9 (LOW, link 1): lines that need not exist / stale docstring

**Dead CALL branch:** `layer_fold.py:178-183` (the CALL `route_order` re-add) is dead today. No CALL map in `defaults.yaml` holds a `route_order`, so `under` never holds one for it to re-add. `probe5.py` shows the branch is correct.
- Option: keep it with a test that reaches it (a defaults CALL map holding model and route).
- Option: delete the 5 lines.

**Stale docstring:** `domain/campaign.py::Environment` still describes `target` as "the set keys of …". It is now a fold patch whose `null` clears, like `loop`, whose comment was updated.

**Confidence:** high.

## F10 (INFO): Owns and collisions

**Collisions:** none of the eight files bkeys-3 owns is touched (`optimizers/nodes.py`, `runner/inner/tasks.py`, `domain/paired_reading.py`, `domain/results.py`, `domain/arm_reading.py`, `scripts/offline_run.py`, `scripts/offline_world.py`, `maintenance/landing.py`). Neither are store-5a's.

**Outside Owns:**
- `domain/campaign.py` (links 1 and 2; the rows name its holders);
- `application/jobs/launcher/head_to_head.py` and `application/preflight.py` (link 2, named in its body);
- `tests/factories.py` (a shared test helper, so a merge-conflict risk only).

**By design:** `assets/behaviour_keys.json` (link 4) conflicts with bkeys-3. Drop the hunk at the combine and regenerate once.

**Line count:** `promptpotter/` is +160/−96. Per link:

| Link | Added | Deleted |
|---|---|---|
| link 1 | +60 | −32 |
| link 2 | +90 | −57 |
| link 4 | +6 | −1 |
| link 5 | +5 | −7 |

F4's fix, if taken, deletes about 4. F9 deletes 5.

**Re-tests at the tip:**
- `**defaults.` count in `authored.py` is 0;
- `replicate=1` is empty;
- `if request.` in `llm_reuse_cache.py` is empty.
