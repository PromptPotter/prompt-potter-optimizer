"""Ratchet: the package's conceptual surface never moves unexamined, in either direction.

The rules are ``docs/developer/conventions.md`` § Reasoning doctrine ``<surface-ledger>``; a
move's reason goes in the COMMIT BODY, and ``git log -p`` is the history layer. This file is
only where the surface stands now — never a target to reach.
"""

from promptpotter.complexity_ledger import compute_ledger

LEDGER_BASELINE = {
    # +1: `domain/command_kinds.py` — the `/commands/{kind}` vocabulary, moved out of the
    # dispatcher that applies it. Not a new concept: the four parties that must agree on it
    # (dispatcher, router, CLI, TS codegen) could not all import the dispatcher, and the one that
    # could not is the CLI — which is why nothing bound the terminal to the command set and five
    # kinds shipped browser-only. The module is what makes `CLI_VERB_FOR_KIND` expressible.
    # +1: `application/jobs/capacity.py` — how many campaigns the machine admits right now, which
    # was a startup constant and is now asked per admission. It folds into neither neighbour:
    # `quota.py` answers per-USER limits, and `registry.py` is a slot counter that must not learn
    # the LLM layer to read provider back-pressure. One module is what lets the number LOWER under
    # a stalled provider without a restart, and lets it do so where it cannot also raise.
    # +1: `application/jobs/launcher/admission.py` — the prologue every launch runs before anything
    # irreversible, held by three launchers as three copies. A module rather than a function on one
    # of them because the CLI must reach it too and cannot import a web launcher; the copies it
    # replaces are the reason the terminal ran no admission at all.
    # +1: `application/jobs/interlock.py` — the two facts about the machine-global jobs dir that
    # must outlive a process: who may admit, and whether a job's producer is alive. It is a module
    # and not a `registry.py` private because both are OS-lock semantics with their own failure
    # mode (reentrancy, release-on-death, token reclaim), and the registry beside it is a slot
    # counter. It PAID for itself: `reconcile_stale` and `reaper.producer_gone` both went, and the
    # injected `producer_gone` oracle with them — one liveness rule now, in one place.
    # +1: `connectors/harbor.py` — a fourth backend, and by construction a connector is exactly
    # one module: the whole point of `connectors/` is that adding one touches no other file. The
    # surface it buys is a containerized agent episode as a measured cell, which is the first
    # backend shape whose row is graded by a verifier rather than matched against a label.
    # +4: `judges/` — `protocol.py`, `simpleqa.py`, `call.py`, `__init__.py`. The surface it buys
    # is an LLM-as-judge as a measured observation, which is what a dataset whose answer is free
    # text has no other way to score: `exact_match` on a bold span cannot grade a factoid, and
    # three datasets already record being blocked on it. It is FOUR and not one because a judge is
    # not a connector — the protocol is a public extension point, the built-in rubric is verbatim
    # third-party text that must not sit in the same file as the registry that validates it, and
    # `call.py` is a second LLM chokepoint on purpose: routing grading through the optimizer's
    # would bank judge spend in the loop's bucket, which is the boundary this whole arc draws.
    # +1: `judges/grounding.py` — the two graders that read a cell's EVIDENCE rather than its
    # answer, which is what makes the `retrieve -> ground -> answer` step schema measurable. Not
    # foldable into `simpleqa.py`: that module's whole discipline is that its text is upstream's,
    # quoted down to its typos, and these two rubrics are ours — a reader must be able to tell
    # which is which without reading the git history. It pays for itself in the same commit by
    # collapsing the ask-parse-verdict body every judge repeated into `call.py::graded`.
    # +1: `infrastructure/llm/capabilities.py` — which decoding parameters a MODEL accepts,
    # resolved operator-override → per-tenant provider snapshot → unknown. Not foldable into
    # `registry.py::_MODEL_PROFILES` beside it, and the split is the point: that table is
    # evidence WE measured and ships in the wheel, this is a third party's claim that goes stale
    # on their schedule and is cached per tenant. Merging them makes one file both evidence and
    # cache, with no way to say which layer answered. It pays for itself immediately — the node
    # ladder and the model's are different sets, and conflating them let a campaign search
    # `reasoning_effort` on a model that does not take the parameter at all.
    # +2: `application/diagnostics/probe_reasoning.py` + its `cli/commands/probe_reasoning.py`
    # shell — the verb that FILLS the evidence table above. Without it `_MODEL_PROFILES` is a
    # hand-list against a catalogue of hundreds, which is the shape that goes stale and then answers
    # wrongly; the whole reason the table may narrow a search axis is that a human can cheaply
    # re-measure it. It runs through `get_llm_client().chat`, not a raw request, so it reports what
    # this repo SENDS.
    # +1: `runner/inner/spawn_context.py`. One module bought 24 deferred imports, because the two
    # halves it separates point opposite ways: publishing an inner-spawn context is something the
    # ordinary runner does on its way past, while RUNNING an inner campaign reaches back down into
    # that runner. Sharing one file made `entry <-> spawn` mutual and `seed_screen` a third leg.
    # +1: `application/diagnostics/ab.py` — the `ab` verb's session half, beside `verify.py` /
    # `noise_floor.py`. The CLI shell held it and opened the session off the ACTIVE pointer, which
    # is why `ab` could not name a campaign. The replay core stays beside the replayers it shares
    # with resume (`mask/verdicts.py`), and the half that calls `init_services` cannot join it
    # there without a runtime `optimization -> initialization` edge.
    # +3: `application/commands/` (an empty `__init__` plus three modules for one). The dispatcher
    # imports no FastAPI and the CLI verbs dispatch through it, so it is application code. Split by
    # importer: `payloads` alone serves the TS builder, `checkin_dispatch` serves dataset ingest
    # and CLI `new`, and `dispatcher` holds the appliers.
    # +6 (three of them `__init__`s): `application/evidence/` splits the evidence read into the
    # address grammar (`subjects`, all an entry point that only ADDRESSES a subject imports), the
    # disk walk (`read`) and the two pure statistics over its rows (`comparison`, `grid`); and
    # `diagnostics/` + `maintenance/` gather the verbs loose at `application/`'s top level.
    # -2: `application/sweep_batch.py` + `infrastructure/store/sweep_store.py` — the sweep batch goes.
    # +1: `domain/rendering.py` leaves domain as two `application/views/render/` modules, each named
    # for what it holds — `optimizer_prompt_text`, whose definitions shape the optimizer prompt, and
    # `prefix_reading` — because no one name covers both.
    # +1: `domain/launch_limits.py` — the one run-limit carrier the wire payloads, admission, the
    # runner and every ingress import; it replaces `RunLimitsPayload` and `RunMode.halt_at_accuracy`.
    # +1: `domain/value_tree.py` — the vocabulary for ONE addressable tree of every value an arm may
    # hold. A module rather than fields on `PipelineSchema` because the two facts it adds are the
    # CONNECTOR's: which channel a value travels, and whether the model sees it eagerly, on demand,
    # or never because it is not text. That is what lets a prompt field, a tool and a step be leaves
    # instead of subsystems — an Agent Skill is a directory of instructions, resources and programs,
    # and an MCP tool is a name, a description and a schema. It PAYS for itself: `node_param_keys`
    # projects off it instead of walking the same declarations again.
    # +1: `application/scoring/cell_envelope.py` — the wall clock ONE measured cell may spend,
    # enforced at the single seam a cell is measured. It is a module and not a helper beside
    # `sample_measurement.py` because three things move together and none of them is measurement:
    # an `asyncio.timeout` scope, the give-back that keeps it measuring the cell rather than the
    # box (the shared limiter's queue, a machine sleep), and the UNSCOREABLE resolution that stops
    # a truncated trajectory being graded. It PAID for itself: `runner/inner/spawn.py` held all
    # three for L4 alone, so harbor's container episodes and every remote cell were unbounded in
    # sum, and it is now a connector declaration any backend can make.
    # +1: `infrastructure/llm/spend_book.py` — the ONE admission of a paid request, held at its
    # worst case before it is sent. Every ceiling compared a cap against spend already recorded,
    # so each call out when it tripped landed past it ($0.1018 on a $0.10 campaign) and a
    # cancelled call billed with no record. It PAYS for itself: the SDK retries, the two caller
    # 429 loops, the deadline re-send and the three caller emits fold into the one send seam.
    "modules": 357,
    # +1: `application/commands/__init__.py`, empty — importers name the submodule.
    # +3: `application/{evidence,diagnostics,maintenance}/__init__.py`, empty for the same reason.
    "init_files": 53,
    # +1: `judges/__init__.py` — flagged for the same reason `connectors/__init__.py` is, and by
    # the same text test: a registry module has both an `__all__` and imports. Named rather than
    # emptied; the protocol types are deliberately NOT re-exported through it.
    "reexport_shims": 6,
    # +1: `CampaignConfig.judges` — which LLM-as-judge grades this campaign's cells, on which
    # models, and under which TERM. One leaf though it nests twice: `Knob` marks a field as a leaf
    # whatever its shape, and how a campaign grades a cell IS one decision however many steps it
    # takes. `Scope.DATA` because swapping a judge invalidates every verdict taken under the old
    # one; `Estimand.GATE` because it decides what counts as a correct answer.
    # +1: `CampaignConfig.accuracy_ceiling` — the accuracy a best-reachable prompt would score on
    # this dataset at this model, which `rounds_to_ceiling` counts rounds against. It folds into no
    # neighbour: `dataset_split` is a fold SIZE and `headline_metric` picks which existing number
    # renders, while this one is a claim no code can derive — only the dataset owner holds it, and
    # the clock reported nothing on any benchmark until a field existed to say it. `Scope.POLICY` /
    # `Estimand.DISPLAY`: it moves no gate, no selection and no stop.
    # −1: `CampaignConfig.allowed_models` — folded into `optimizer_narrowing`, which already
    # carried a per-node `param_allowed_values`. With `model` a real search axis, "which models
    # may this node run" and "which models may a human steer a fork to un-tainted" stopped being
    # two questions, and the second field could only disagree with the first. Its command kind,
    # store method, CLI verb and dashboard panel went with it.
    # +3: `DeterminismClamp.{temperature,seed,route_order}` — what a campaign pins on every
    # optimizer call. Three leaves and not one: the draw and the HOST are different decisions with
    # different evidence behind them (a temperature is read off the noise it removes, a route off
    # `served_by` in the ledger), and pinning either without the other is a real, common state.
    # They fold into no neighbour — the node's pipeline file is install content shared by every
    # campaign, so a per-campaign pin has nowhere else to live, and `pipeline_overlay` addresses
    # BACKEND nodes. `Scope.POLICY` because past rounds stay valid; `Estimand.SEARCH` because what
    # moves is the optimizer's trajectory. They replace `InstrumentMode.optimizer_clamp`, which was
    # reachable only from inside an L4 inner asyncio task.
    # +1: `OptimizationConfig.escalation_ladder` — which LAYERS a campaign may escalate to. It
    # folds into no neighbour: the three patiences PACE a ladder and can only defer a fire, and
    # the L1 / L1+L2 / full ablation needs an arm where L2 cannot fire at all. Every earlier
    # attempt at it was a patience set high enough to outlast the round budget, which is a
    # property of the budget rather than of the arm.
    "config_leaf_fields": 44,
    # +1: `QUEUE_MAX_WAIT_S` — how long a launch may wait in line before it is withdrawn. It is a
    # setting and not a constant because it is the one queue number a HOST has to be able to
    # answer for: on a shared box it decides when someone else's waiting launch is given up on.
    "settings_env": 32,
    "settings_const": 14,
    "opt_search_point_fields": 39,
    # +1: `theta_caveat` on `ScoredCandidate` and `ScoreboardRow` — the per-ARM half of
    # `ThetaCaveat`, so a floor-pinned arm's θ is disclaimed on the row it invalidates rather
    # than only on the round's scale reading. A served state, not a derived one: the rows a
    # client would test are the per-sample arrays the candidate row exists to avoid shipping.
    # +1: `sp_hash` on `ScoredCandidate` — the searchpoint id, so a candidate names the archive
    # rows it paid for. Cannot be derived from what the model already carries: the sibling
    # `resolved_pipeline_params` has the rendered prompt stripped, and the hash covers it.
    # +1: `parent_results` on `RoundResult` — the bar the round's arms were measured against, on
    # the round's own subset. Not derivable from anything banked: round N-1's winner is the same
    # SEARCHPOINT but was read on cells this round never bought, and reconstructing it that way
    # is what left a sample-set mask re-scoring the arms and not the bar.
    # +3: `input_tokens` / `output_tokens` / `cache_read_tokens` on `ScoredCandidate` — what
    # MEASURING one searchpoint consumed, folded once beside the `cached_samples` it is the peer
    # of. Not derivable from what the model already carries: the counts live in each row's
    # `pipeline_data.step_tokens`, and those rows are exactly the per-sample arrays the candidate
    # row exists to avoid shipping. The same three names `DashboardSample` carries one level down,
    # so this is the existing vocabulary at a second arity rather than a fourth spelling.
    # +1: `not_attempted`, on `RoundResult` and on the `DegradationHealth` nested in it — cells of
    # the panel the walk never SENT. Not derivable from the rows, and that is the whole point: they
    # carry no row, because an abort used to fabricate one per unreached cell and so gave absence
    # the shape of failure. With the padding gone the counters are honest but the round can no
    # longer tell "measured badly" from "barely measured", which is the difference between grading
    # a pipeline and asking for a re-measure. One name, two arities — the round's and its verdict's.
    # +1: `unscored`, at the same two arities and for the reason its neighbour exists: cells the
    # walk DID send and measure, carrying no verdict because the formula names a term their rows do
    # not. No subtraction finds them — an ungraded cell was attempted, so it is inside `samples` —
    # and the number is the WINNER's, which the rows at this altitude cannot say. Without it a round
    # that graded six of ten reads as one that graded ten. Distinct from `not_attempted` because the
    # remedies invert: a cell never sent is re-run, an ungraded one re-graded off the banked row.
    # 166 -> 213: the COUNTER was corrected, not the package — no surface moved for this raise, so
    # the total is not comparable across it and the ROWS are what to read. `_count_leaves` deduped
    # on one `seen` set shared across sibling branches, so a model reached at a second field
    # contributed ZERO leaves: `SpendBucket` is declared four times on `SpendRollup`
    # (backend/loop/judge/diagnostic) and was priced once; `RuntimeFailure` and `ValidationFailure`
    # twice each, on `OptSearchPoint` and on `RoundResult`. A row whose whole purpose is to price
    # nested surface was blind at exactly the sites that have it — deleting one of the four buckets
    # lowered it by nothing, and adding a fifth would have raised it by nothing. The guard is the
    # walk PATH now, so a genuine cycle still returns 0 and a second declaration costs what it is.
    # +17: `DegradationHealth`, reached through `RoundResult.health`. It was priced as ONE leaf
    # because its annotation was still a forward reference at count time — so the row read 213 or
    # 230 depending only on whether anything in the process had validated a `RoundResult` first,
    # and the gate saw 213 because `test_complexity_ledger` sorts ahead of every file that builds
    # one. The walk rebuilds each model now; nothing was added.
    "cycle_result_fields": 230,
    # +1: `judges/__init__.py::_compute(**_: Any)` — the `Evaluator.compute` a judge becomes. The
    # materializers pass `result` and `schema` to every evaluator, and each one absorbs the kwargs
    # it does not read; every compute fn in `scoring/evaluators.py` has the same tail for the same
    # reason. Narrowing it would make this the one evaluator the shared call site cannot invoke.
    # -1: `pipeline_schema.py::PipelineSchema.model_post_init(self, __context: Any)` is GONE — it
    # cached `_node_map` at init and pydantic skips it on `model_copy`, so a narrowed schema
    # answered with pre-copy nodes. Derived on read now. A real subtraction, not a re-annotation.
    # -1: `candidate_diff.py::parent_param_value(…, proposed: Any)` — it read the proposal only to
    # know which fields a nested description dict named; one key per path names its own.
    # -1: `evidence/read.py::_coerce_state(raw: Any)` is GONE with the overlay-keyed edit identity
    # it existed to canonicalise. `sp_hash` is a stamped string, so nothing coerces a nested dict.
    "any_params": 48,
    # +1: `results.py::is_floor_pinned(rows: Sequence[Mapping[str, Any]])`, the same signature as
    # `measured_cells` and `is_answer_collapsed` beside it — a round row read off disk is a plain
    # mapping, so a narrower annotation here would be a claim the callers cannot honour.
    # +4: `projection_envelope.py::ray_payload` and its `_pick` helper — the ray's field
    # projection. Both ends are genuinely untyped: the input is a `CycleRecord.model_dump()`
    # whose bulk sits under `payload: dict[str, Any]` on the models themselves, and the output is
    # `RayItem.payload`, the same shape narrowed. A model for the projection would have to
    # declare every kind's picked subset as a class, which is the hand-authored roster the
    # declaration exists to avoid.
    # Back to 88: `scoring.py::is_verifier_graded` was added taking a `Mapping[str, Any]` row and
    # now takes the LABEL (`str | None`). Not a cosmetic narrowing — the question has two carriers
    # (an unmeasured `Sample`, where the fact is `None`, and a measured row, where it is `""`), so
    # a row signature could only ever serve one of them and the other would have re-derived it.
    # Its set arity `all_verifier_graded` takes labels for the same reason and adds none back.
    # +1: `connector.py::CellEnvelopeSeconds.__call__(…, pipeline_params: dict[str, Any] | None)` —
    # the SAME pair `WireAdapter` beside it takes, and deliberately so: what a cell may spend is
    # decided by the query and the candidate's params, which is exactly what the request is built
    # from. A narrower annotation here would be one the connector's own adapter cannot honour.
    # +2: `pipeline_overlay.py::steers_disallowed_model` — the frozen campaign config and the
    # overlay, which is the pair the babysit verdict is a function of. Both are genuinely
    # untyped maps: `Campaign.config` is a `dict[str, Any]` snapshot and an overlay is
    # `{node: {param: value}}`, the same spelling every function in this module already takes.
    # Naming the two-step ONCE is what let the browser's copy of the rule be deleted; it had
    # answered off a different permitted list than `fork-cycle` dispatch did. Taking `Campaign`
    # instead would buy one of these back and couple a pipeline-SHAPE module to the manifest
    # entity, which is the worse structure of the two.
    # +1: `permitted_models_for_campaign` — the campaign->permitted hop named apart from the
    # verdict, so a surface can SERVE the comparison set instead of deriving a second one beside
    # it. Its return is typed; only the `Campaign.config` snapshot param counts.
    # −2: `EscalationSignal.to_dict` and `ValidatorOutcome.to_dict` — neither had a caller, and the
    # signal's candidate position they would have serialized was read by nothing either.
    "domain_any_maps": 90,
    "models_lax": 3,
    "prompt_string_fields": 6,
    "injections": 32,
    # +1: `l1_only_ladder`. The L1 / L1+L2 / full ablation needs an arm where L2 PROVABLY never
    # fires, and a rule is the only place that can be true of: the router is the whole policy, so
    # a gate at the caller would leave the policy saying FIRE_L2 while the loop quietly did not.
    "escalation_rules": 7,
    # DEBT, and the only row here whose whole purpose is to fall. A function-local import of our
    # own package is habit, and the habit is the defect: unmarked, it cannot be told from a
    # load-bearing one, so nobody can hoist safely or add one knowingly. The three reasons people
    # reach for, and the measurement that killed each: `conventions.md` § Code shape.
    # -262: the sweep. What remained was 52 across 8 files, every one a RUNTIME cycle participant.
    # -16 -8: the `spawn_context` split (see `modules` above) — one boundary move, and the two
    # biggest knots fell together, which is what a boundary being in the wrong place looks like.
    # -5: `runner/entry`, which was only ever entangled through `spawn`.
    # -14: the connector and injection tables complete in cached accessors, at a declared step.
    # -1: `connectors/promptpotter.py` imports `spawn` at top level: `instrument_of` lives in
    # `domain/l4/inner_origin.py`, so no cycle closes through `evidence`.
    # +3: an own-package `importlib.import_module` counts too, which the `import` statements alone
    # never showed — the two registries' member walks and the CLI's `COMMANDS` table.
    # -1: `campaign_runner.py::COMMANDS` now names each handler at module scope instead of
    # resolving `"module:attr"` through `importlib.import_module` on dispatch. Its own startup
    # justification measured false: `_shared`/`reaper` already ride most handlers' own module-scope
    # imports, so `--help` paid their cost either way; hoisting the 24 handlers adds only their
    # verb-specific tail (numpy for `ab`, httpx for `new`).
    # Of the 10, all are deliberate: `complexity_ledger`'s own 7 (it counts every layer, so it may
    # import none at module scope), `escalation/state` (1, documented there) and the two walks,
    # which import each member when the table completes. Count cycles with care: an
    # `if TYPE_CHECKING:` import sits in the module body and reads as top-level to an AST walk,
    # which made three "pairs" that were never runtime edges. The files whose cycle is invisible
    # until the build breaks say so at the import.
    "deferred_imports": 10,
    # +1: `judges/CLAUDE.md` — the per-layer contract for a new top-level package, indexed from
    # `promptpotter/CLAUDE.md` like every other. It earns a page rather than a section in
    # `connectors/CLAUDE.md` because its load-bearing rule is the OPPOSITE concern: a connector
    # says where a measurement comes from, a judge says a grader is a measurement and never a
    # formula term — and that rule is what stops six re-derivation sites re-billing the archive.
    # +1: `application/evidence/CLAUDE.md` — the evidence rules, apart from `application/CLAUDE.md`
    # so only a reader editing that package pays for them.
    "claude_md": 9,
    # SIX by charter (`tests/CLAUDE.md` § What each file is for). This row never rises: a test
    # rides an existing file's existing section, or it is not written.
    "test_files": 6,
    # A function is admitted through the three axes — behaviour-coupled, silent, unrecoverable —
    # and a raise here names the invariant and the axis that was hardest to clear.
    # +1: the provenance sink must not move the merge it observes — `resolve_pipeline_config_params`
    # is hashed into the origin cycle id, so a served read that perturbed it re-keys the campaign.
    # Silent, and it orphans every banked row. (test_integrity § 1)
    # +1: a schema copy answers for itself — `model_copy` skips `model_post_init`, and a cached
    # index let L1 propose and the gate admit models the mint had closed. Silent misspend on an
    # axis the operator shut. (test_integrity § 4)
    # +1: a reused origin seeds exactly the config it ran — `overlay_from_campaign_config` must
    # invert `split_overlay`, and `param_keys: []` is a real, falsy declaration. What the pair
    # loses is a setting the new campaign runs without, unreported. (test_integrity § 4)
    # +1: a check-in resolves the config its START will freeze — the arm served the manifest's
    # frozen snapshot, which for a campaign that has run nothing is whatever the SHARED slug file
    # last said. Silent: the operator confirms a campaign that runs something else. (§ 4)
    # +1: a check-in with no overlay still resolves its backend floor — no dataset dir means
    # `readable_dataset_dir` finds nothing, and the schema fell back to zero nodes. The node editor
    # hangs on "Loading", which is the one failure a surface CANNOT recover from. (§ 4)
    # +1: narrowing an enum never deletes the values it unticked — `narrow` REPLACES
    # `param_allowed_values`, so a menu that is its own selection can never offer a rung back.
    # Unrecoverable for the life of the campaign, and nothing says a position was removed. (§ 4)
    # +1: the draft's CHAIN reaches the mint on a reused dataset — a reused slug writes no
    # `pipeline.yaml`, so the operator's pipeline toggle reached nothing. Silent: every measurement
    # describes a program nobody chose, and the picture renders the chain that RAN. (§ 4)
    # +1: an axis no agent moves is SHUT rather than exempt — the reach arithmetic, ported from the
    # browser derivation it replaces rather than deleted with it. A wrong count renders a plausible
    # number, which is the silent half; three TS cases collapse into one here. (§ 4)
    # +1: a measured point is served the identity it RAN under — identity is recomputed per read,
    # right on the run path and wrong on a finished one, so a moved instrument was served beside a
    # run that used another. Silent, and it was 8 live mismatches. (§ 4)
    # +1: an axis is bounded by the model that would RUN it. The ENGINE's resolve had no guard —
    # the only test of the rule was a vitest over the browser's MENU derivation, which is a
    # different question and stays. Both directions here are silent and paid: an unoffered rung
    # buys a 400 that costs the candidate its whole panel, a wrongly withheld one deletes a search
    # position and the round still elects. The empty case is why: read falsy, an axis with nothing
    # legal becomes an unbounded one. (§ 9)
    # +1: a run's backend row names the endpoint it actually REACHED — the resolver checked only
    # that an id existed, so a `local` row pointing elsewhere absorbed the run and every measurement
    # it banked was attributed to a backend nobody pointed it at. Silent in both directions, and no
    # re-run re-attributes what is already on disk. (test_integrity § 8)
    # +1: a campaign's FROZEN config decides what it runs. `apply_inherited_overlay` carried two
    # fields off the snapshot and rebuilt the rest from the dataset template, so a mint-time
    # ceiling landed in `campaign.json` and never in `run_limits` — the loop enforced one number
    # while every surface reading the campaign showed another. Silent by construction: both
    # numbers are real, and only a run that outlives the file's ceiling tells them apart.
    # (test_resume § the frozen-ceiling case)
    # +1: a measured point is served the SCHEMA it ran under. `output_schema_descriptions` is a
    # core always-on axis, so L1 moves it every round; the wire folded the new prose in and the
    # served contract read the parsed DECLARATION, so every surface showed the pre-evolution
    # schema with nothing saying the two had parted. Silent, and unrecoverable by re-reading —
    # the display was wrong for the whole life of the campaign. (test_integrity § 4)
    # +1: an axis the picked MODEL refuses offers only the value it runs. A key outside the
    # endpoint's `supported_parameters` is dropped on the way out, so L1 searched a space where
    # every value produced a byte-identical call and the round scored the difference as signal.
    # Reported by a badge and never enforced on the search. (test_integrity § 4)
    # +1: a narrowing layer answers for ITS params and no others. `merge_node_blocks` merged
    # `optimizer` one level, so a connector naming one rung list REPLACED the backend's whole
    # `param_allowed_values` — every other axis on that node lost its declared space while
    # staying open, which handed L1 a bare string where a two-value toggle was declared.
    # (test_integrity § 4)
    # +1: every LLM node is offered the text-or-structured toggle. Whether the request carries a
    # schema is PromptPotter's own lever, so a backend declaring it too made two mechanisms for
    # one thing — and TermNorm's `output_schema` silently outranked its `response_format`, so
    # every arm of the axis L1 was being offered produced the identical call. (test_integrity § 4)
    # +1: answering in TEXT sends no contract to answer into. `answer_field` must leave with the
    # schema — destructuring a slot the response never had reads "" for every sample and grades
    # the run NO_RESULT, a mechanical zero the loop would charge to the idea under test. Pairs
    # with the byte-identical claim for an unmoved node: the fold runs before the content hash,
    # so a resolved default written here would re-key the whole archive. (test_integrity § 4)
    # +1: the one stop the loop fires with no human in the way read `accuracy`, not the number a
    # round is won on, so it ended campaigns whose objective still had somewhere to go — and did it
    # silently, because `perfect_score` is a SUCCESS outcome and every number renders.
    # +1: a verify's SIZE, now that the loop fires one by itself and the count is no longer a
    # number a human typed. Behaviour-coupled and silent the same way: a wrong size still produces
    # a verdict, just an unaffordable or an empty one. (test_numerics § 10)
    # +1: a prompt field the check-in locked is neither offered to L1 nor accepted from it. The
    # engine force-kept all six open, so a lock drawn on one was honoured by nothing and a rewrite
    # of the operator's text could win the round unreported. (test_integrity § 4)
    # +1: an output-schema field's prose locks per field, and a field added under a held one stays
    # held. One `object` param reached top-level fields only and locked all or none, so a nested
    # field's prose was unreachable and a locked one could not be told apart. (test_integrity § 4)
    # +1: moving one spend ceiling leaves the other at the cap its launch composed. Merged against
    # the job's reservation instead, the untouched arm would land in `spend_cap.json`, which the
    # gate prefers, and a USD raise would lift the token ceiling to the account's headroom.
    # (test_security)
    # +1: a cell the active formula cannot grade keeps its measurement rather than being banked a
    # failure, which costs the candidate's whole remaining walk and kills every later replay of an
    # archive that is fine on disk. (test_resume)
    # +1: whether a harbor episode OPENED the skill its prompt IS. Unopened is a no-skill episode,
    # so a round of them is arms-all-identical read as a tie — and a silent layout drift reads 0.0
    # on every cell, which is indistinguishable from exactly that finding. (test_integrity § 1)
    # +1: the panel L1 rewrites offers each field's OWN value — shown the spliced
    # `problem_description`, a faithful rewrite hands the operator's framing back as the raw field
    # and the next render splices it around that copy, once per elected round. Silent, and every
    # candidate after it is scored on the grown prompt. (test_integrity § 3)
    # +1: the COLLAPSED cut on a bank with no labels — the gate keyed on a truth SET, so on every
    # benchmark the preprint runs it was permanently False and the constant answerer bought its
    # whole budget. Both directions are pinned in one function because the verifier's evidence
    # cuts both ways: a cell left unsolved convicts, and an arm that solves every cell alike is
    # degenerate and CORRECT, so the ceiling exemption is what stops the fix eating the best arm.
    # (test_numerics § PoBB elimination)
    # +1: a campaign that pins its draw and its route RUNS pinned, and two pins bank apart. The
    # clamp merges last or `l1_generate`'s per-call `temperature` survives it; the route reaches
    # `hash_call` or one host's answer is replayed under another's name. Silent both ways — every
    # number renders — and the banked row cannot be told from one the pinned route produced.
    # (test_integrity § 1)
    # +1: an L1-only ablation arm that escalates once has measured something else under the arm's
    # name, and nothing on any surface tells the two apart. (test_numerics § 10)
    # +1: a resumed cycle's wall clock folded every earlier launch against this launch's endpoints,
    # so each round closed before the resume read as instant in the published clock. (test_resume)
    # +1: the ADR-0005 babysit trigger's own decision boundaries. The 13 vitest cases deleted with
    # the browser's copy of the rule were the ONLY automated assertion of that truth table
    # anywhere, and a false negative is silent twice over: the fork is admitted without
    # `campaign.babysit` AND the branch is never stamped grade C, so it enters clean comparison,
    # origin reuse and the L4 rollup as untainted with every number rendering. (test_security)
    # +1: a human-authored arm pooled with the L1 arm that shares its `arm_id`. The read groups
    # replicates by optimizer-prompt hash, which two forks of one campaign hold identically, so the
    # paired test the human-in-the-loop claim is published from compares an arm against itself and
    # reports the real difference as this instrument's noise. Every number still renders.
    # (test_numerics § 7)
    # +2: a cell a declared bound CUT read as a hole in the measurement at BOTH readers. The same
    # declaration cuts the re-measure, so the repair branched the cycle and re-bought the cell
    # (test_resume), and the panel gate halted the live round advising the resume that re-buys it
    # (test_numerics § 10) — unbounded forks and unbounded spend, every number rendering.
    # +1: a cell that RAN and had no grade dropped its spend at `measure_sample`'s one catch, so
    # the ceiling under-counted a paid episode and the sidebar read $0.00 (test_integrity § 7).
    # +1: a harbor episode a provider throttle ran out of clock banked its verifier's 0.0 as the
    # model's grade, and the archive replays it under that identity; the same test holds an
    # account out of credit to a halt rather than a panel of holes (test_integrity § 1).
    # +1: a harbor campaign declaring `skill_delivery: system_prompt` whose skill never reached
    # the first request runs every arm prompt-less under that mode's key (test_integrity § 1).
    # +1: a look-ahead walk launches one cell past its stop rule's horizon, so a horizon that
    # answers after the real cut discards paid calls unrecorded (test_numerics § 5).
    # +1: a burst of concurrent sends — answered, cancelled, timed out — records spend past its
    # ceiling, or a send that never reported is billed with no record (test_security).
    # +1: a campaign list row naming a model its root does not run — the shared file's or the
    # frozen delta's instead of the root seed's — and the campaign read without `at` skipping that
    # seed; every row renders, and siblings are told apart by the wrong model (test_integrity § 4).
    "test_functions": 196,
    # Every property the generated contract offers the browser. A field with no reader is the
    # shape this row exists to price: `NodeReach` and `permitted` were both served, neither was
    # ever read, and nothing counted them until here.
    # +10: `CampaignPipelineResponse` — one campaign's resolved pipeline, which no schema answered
    # before; the four surfaces that each re-derived it read this instead.
    # +1: `NodeConfigParam.source` — which layer won a param, which is what makes the served value
    # readable as an answer rather than a number the browser has to attribute itself.
    # +5: `OptimizerPipelineResponse` — served all along behind a bare dict, so the browser held a
    # hand-written copy of it. Declaring the shape is what deletes the copy, not a new surface.
    # +1: `CampaignPipelineResponse.nests` — the L4 drill-in, read by `useConnector` and opened by
    # `useNestedPipelines`. It rides this read because a nest is a fact about the chain, not a hop.
    # +5 +3: `NodeReach` and `reach` on the three doors that serve config rows — the browser summed
    # `movable_by` itself, against whichever schema the caller happened to hold, which on a campaign
    # read was the DATASET's. `nodeReach` is deleted in the same commit; this is a MOVE.
    # +1: `NodeConfigParam.permitted` — the babysit gate's own set, where it differs from the menu.
    # One list cannot say both, and serving only the menu left a model pickable that taints on
    # confirm. Read by `permittedModels`, which replaces `permittedModelsFromNarrowing`.
    # +1: `CampaignPipelineResponse.is_single_node` — a SEARCH-SPACE fact, not a step count, so no
    # surface guesses it by counting. Read by `useConnector`; the client `singleNode` goes with it.
    # +1: `ModelCapability.indistinct_efforts` — rungs MEASURED to produce the same call on this
    # model, THREE-STATE, so `None` unprobed and `[]` probed-all-distinct stay apart. It is what
    # lets the offered ladder be every rung minus only what an endpoint REFUSES: an axis keeps every
    # legal value, and the sameness is reported instead of being enforced by deletion. It cannot
    # ride `reasoning_note` — prose no surface can branch on is not a state.
    # -2: `ConfigCoupling.knobs` and `.estimand` — served to every config-map reader and opened by
    # none. `labels` is the display projection of the same walk and IS read, so the dotted paths
    # beside it were the raw form of a thing already answered; `estimand` is the grouping key the
    # panel never groups by. Dropping them narrows what a browser can start depending on.
    # +2: `ForkPreviewRequest.pipeline_overlay` + `ForkPreviewResponse.steers_disallowed_model` —
    # the ADR-0005 babysit verdict, which `fork-cycle` dispatch reached and never returned. Two
    # served fields buy the DELETION of the browser's own copy of the rule
    # (`overlaySetsModelOutsideAllowed`, `costLeverKeys` and 13 vitest cases), which answered
    # off a different permitted list than the gate did. It folds into no neighbour: every other
    # campaign read is addressed by id, and this one's subject is an overlay that exists nowhere
    # on disk until the fork is confirmed.
    # +1: `ForkPreviewResponse.permitted_models` — the set the gate compared against, served
    # beside its verdict. Without it the panel named models off the served pipeline rows while the
    # verdict came off the frozen campaign narrowing, so the sentence and its answer could disagree
    # on a cycle seed that moved the set. Per node, never flattened: the panel flattens for one
    # sentence, which is display, not derivation.
    # +1: `SubjectReading.authorship` — WHO proposed the configuration a subject reads at. The read
    # could already compute the human-against-loop contrast and had no key to LABEL or group it by:
    # `arm_id` hashes round 0's optimizer prompts, which every fork of one campaign shares whoever
    # wrote the edit. It folds into `arm_id` only by destroying that distinction, and into `label`
    # not at all — a label names the branch, not its author.
    # +1: `SubjectReading.human_intervened` — whether an operator touched the subject's CYCLE
    # mid-run. Apart from `authorship` because it is about the run rather than the point: a
    # loop-authored arm carries it too, and a babysat cycle is not comparable to an untouched one
    # however it was authored. Already on `index.json`; nothing served it to a comparison.
    # +1: `SubjectReading.cached_samples` — how many of the point's cells REPLAYED. The effort half
    # of the same pairing: a rewind fork inherits its parent's rows, so `n_cells` beside it reads
    # that evidence as independently bought. `int | None`, because 0 is the measurement "it earned
    # every cell" and absent is no report — collapsing them is what credits the inherited arm.
    # +1: `RankedEdit.campaign_id` — the other half of that row's identity, once the ranking keys
    # on `sp_hash` and pools within one campaign. Two campaigns that ran the same searchpoint hold
    # one hash and are two rows, each measured against its own origin, so serving the hash alone
    # collides them on every table that keys a row. `provenance[]` carries the same id per
    # occurrence and cannot replace it: a list expresses no guarantee that the row HAS one.
    # (`RankedEdit.state_hash` → `sp_hash` is a rename in the same commit and moves nothing.)
    # +1: `DiagnosticRunRecord.held` — did the verdict hold on the wider set. The pane drew the
    # answer already; what it did not have was the TOLERANCE, so the browser picked its own
    # epsilon for when two measured rates count as equal. It folds into neither rate beside it:
    # those are measurements, this is the comparison's own decision, and the one reader it buys
    # is the bar that was authoring it. (`RoundSummary.separable` lands in the same arc and costs
    # nothing here — `dashboard.json` is served verbatim and reaches no OpenAPI schema.)
    # +4: `CampaignSummary.spend_used_usd` + `.spend_unpriced_tokens`, counted twice because
    # `CampaignDetailResponse` inherits them. The sidebar row prints a campaign's dollars, and the
    # only other money carriers are per-cycle (`dashboard.json`, cumulative from the seed, so a
    # fork double-counts) or per-account (`QuotaStatus`); adding up cycles in the browser is the
    # arithmetic `webapp/CLAUDE.md` forbids. The pair mirrors `QuotaStatus`'s spend + unpriced.
    # +3: `MachineStatusResponse.ceiling`, `QuotaStatus.concurrent_queued` and
    # `.max_concurrent_cycles_writable` — the Account panel's run-concurrency section. The machine
    # ceiling was served nowhere, the queued half of the account limit only inside a refusal, and
    # who may move the limit is a server decision the browser would otherwise re-derive.
    # +8: `CampaignSummary.runs_with` (counted twice through `CampaignDetailResponse`), its
    # `CampaignRunsWith.params` + `.max_rounds`, and `RunsWithParam`'s node/key/value/source — what
    # a root runs with, on the list. Sibling campaigns differ only there, and the per-campaign
    # `/pipeline` read is too heavy per row while the frozen `config` is a delta the browser may not
    # merge (I9). A second transport of that resolver's answer, so it folds into no neighbour.
    # (`CycleListEntry.n_rounds` → `rounds_closed` is a rename in the same change and moves nothing.)
    "served_fields": 600,
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
