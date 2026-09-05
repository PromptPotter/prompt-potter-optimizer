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
    "modules": 340,
    "init_files": 49,
    # +1: `judges/__init__.py` — flagged for the same reason `connectors/__init__.py` is, and by
    # the same text test: a registry module has both an `__all__` and imports. Named rather than
    # emptied; the protocol types are deliberately NOT re-exported through it.
    "reexport_shims": 6,
    # +1: `CampaignConfig.judges` — which LLM-as-judge grades this campaign's cells, on which
    # models, and under which TERM. One leaf though it nests twice: `Knob` marks a field as a leaf
    # whatever its shape, and how a campaign grades a cell IS one decision however many steps it
    # takes. `Scope.DATA` because swapping a judge invalidates every verdict taken under the old
    # one; `Estimand.GATE` because it decides what counts as a correct answer.
    "config_leaf_fields": 40,
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
    "cycle_result_fields": 164,
    # +1: `judges/__init__.py::_compute(**_: Any)` — the `Evaluator.compute` a judge becomes. The
    # materializers pass `result` and `schema` to every evaluator, and each one absorbs the kwargs
    # it does not read; every compute fn in `scoring/evaluators.py` has the same tail for the same
    # reason. Narrowing it would make this the one evaluator the shared call site cannot invoke.
    "any_params": 51,
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
    "domain_any_maps": 88,
    "models_lax": 3,
    "prompt_string_fields": 6,
    "injections": 32,
    "escalation_rules": 6,
    # +1: `judges/CLAUDE.md` — the per-layer contract for a new top-level package, indexed from
    # `promptpotter/CLAUDE.md` like every other. It earns a page rather than a section in
    # `connectors/CLAUDE.md` because its load-bearing rule is the OPPOSITE concern: a connector
    # says where a measurement comes from, a judge says a grader is a measurement and never a
    # formula term — and that rule is what stops six re-derivation sites re-billing the archive.
    "claude_md": 8,
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
