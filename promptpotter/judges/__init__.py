"""The judge registry — how a judge becomes available, and how one becomes an ``Evaluator``.

Shaped exactly like ``connectors/__init__.py``, and that is the point rather than a coincidence:
that module is this repo's answer to "make it exchangeable", so a judge author has one pattern to
learn, not two. A ``_BUILTIN`` data dict, a published entry-point group, one validator that runs
over built-ins and third-party plugins alike, a plugin may not shadow a built-in, and a broken
plugin is fatal rather than skipped.

**A judge is not a new kind of thing to the scoring layer.** :func:`build_evaluators` turns a
campaign's term → :class:`~promptpotter.judges.protocol.JudgeSpec` mapping into ordinary
``Evaluator``s at ``per_sample`` scope, which the existing materializer runs once at measure time
and banks into the row. Everything downstream — the formula namespace, the webapp's scoring-mask
editor, the read-side mask's refusal to recompute them — already works.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, cast

from promptpotter.judges.grounding import ANSWER_GROUNDING, EVIDENCE_RETRIEVAL
from promptpotter.judges.protocol import Judge, JudgeSpec
from promptpotter.judges.simpleqa import SEALQA, SIMPLEQA

if TYPE_CHECKING:
    from promptpotter.application.scoring.evaluators import Evaluator
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.stores import LLMReuseCache

__all__ = [
    "ENTRY_POINT_GROUP",
    "JUDGES",
    "JUDGE_ORIGINS",
    "build_evaluators",
    "get",
]
# The protocol TYPES are deliberately absent: import them from `promptpotter.judges.protocol`,
# the same rule `infrastructure/store/__init__.py` holds. Re-exporting them here would give every
# type two import paths and make this module's surface the registry's plus the protocol's.

ENTRY_POINT_GROUP = "promptpotter.judges"
"""Published: a third party ships a judge by declaring this group and touches nothing here.
Renaming it un-registers every plugin at once."""

_BUILTIN: dict[str, Judge] = {
    "simpleqa": SIMPLEQA,
    "sealqa": SEALQA,
    "evidence_retrieval": EVIDENCE_RETRIEVAL,
    "answer_grounding": ANSWER_GROUNDING,
}


def _validate(key: str, j: Any, origin: str) -> Judge:
    """Every invariant, applied to built-ins and plugins alike — that equivalence IS the contract.
    Raises at import, so a half-wired judge can never fail mid-campaign instead."""
    where = f"judge {key!r} ({origin})"
    if not isinstance(j, Judge):
        raise TypeError(f"{where}: expected a Judge, got {type(j).__name__}.")
    if j.name != key:
        raise ValueError(f"{where}: registered under {key!r} but names itself {j.name!r}.")
    if not j.version:
        raise ValueError(
            f"{where}: declares no version. Identity folds the rubric hash too, but a judge whose "
            f"BEHAVIOUR changed without its text changing has nothing else to move."
        )
    if not j.rubric:
        raise ValueError(
            f"{where}: declares no rubric. It is hashed into the measurement fingerprint, so a "
            f"judge that builds its prompt inside `grade` has an identity that cannot see it."
        )
    if not callable(j.grade):
        raise ValueError(f"{where}: grade is not callable.")
    if unknown := set(j.to_score) - set(j.labels):
        raise ValueError(
            f"{where}: to_score scores labels this judge never emits: {sorted(unknown)}."
        )
    if ungraded := set(j.labels) - set(j.to_score):
        raise ValueError(
            f"{where}: emits labels with no score: {sorted(ungraded)}. A label is not a score, so "
            f"the mapping is declared rather than guessed."
        )
    if bad := {k: v for k, v in j.to_score.items() if not 0.0 <= v <= 1.0}:
        raise ValueError(f"{where}: to_score values outside [0, 1]: {bad}.")
    return j


def _load() -> tuple[dict[str, Judge], dict[str, str]]:
    judges: dict[str, Judge] = {}
    origins: dict[str, str] = {}
    for key, j in _BUILTIN.items():
        judges[key] = _validate(key, j, "built-in")
        origins[key] = "built-in"

    for ep in entry_points(group=ENTRY_POINT_GROUP):
        dist = getattr(getattr(ep, "dist", None), "name", "?")
        origin = f"{dist}: {ep.value}"
        try:
            loaded = ep.load()
        except Exception as exc:
            # Fatal, never skipped: skipping trades a loud error naming the package for
            # `judge 'x' not registered` at mint time, with nothing pointing at the cause.
            raise RuntimeError(
                f"judge entry point {ep.name!r} ({origin}) failed to import: {exc}"
            ) from exc
        if ep.name in _BUILTIN:
            raise RuntimeError(
                f"judge entry point {ep.name!r} ({origin}) shadows a built-in. Which object "
                f"answers a built-in key is not a third party's call."
            )
        if ep.name in judges:
            raise RuntimeError(
                f"judge {ep.name!r} declared twice ({origins[ep.name]} and {origin})."
            )
        judges[ep.name] = _validate(ep.name, loaded, origin)
        origins[ep.name] = origin
    return judges, origins


JUDGES, JUDGE_ORIGINS = _load()
"""``JUDGE_ORIGINS`` is the audit surface — a plugin's name is not greppable in this tree, so the
distribution behind every registered key is recorded, ours included."""


def get(name: str) -> Judge:
    if name not in JUDGES:
        known = ", ".join(f"{k} ({JUDGE_ORIGINS[k]})" for k in sorted(JUDGES))
        raise KeyError(f"judge {name!r} not registered. Known: {known or '(none)'}")
    return JUDGES[name]


async def _compute(
    *,
    result: QueryMeasurement,
    judge: Judge,
    spec: JudgeSpec,
    term: str,
    cache: LLMReuseCache | None = None,
    schema: PipelineSchema | None = None,
    **_: Any,
) -> float | None:
    """The ``Evaluator.compute`` a judge becomes. ``None`` on a failed or unreadable grading, which
    OMITS the term rather than banking a zero — the difference between "this answer was wrong" and
    "we did not find out".

    The score is the return value because that is all an ``Evaluator`` yields. The LABEL and the
    EXPLANATION are written onto the row here instead, and that is deliberate rather than a
    shortcut: they are the half of a verdict a human and L1 actually read ("wrong entity" vs
    "right but hedged"), and a judge that produced them with nowhere to put them would be the
    writer-with-no-reader this whole arc exists to stop repeating. ``measure_sample`` banks
    ``pipeline_data`` after this returns, so a write here reaches the archive and the round file.

    **Everything banked is keyed by TERM, never by judge name.** Two terms may run the same
    registered judge — the same rubric on two models, or one step's grader reused — and under a
    judge-named key the second verdict would land on top of the first, leaving one label to
    describe two gradings. The term is unique by construction: it is a mapping key.

    The reuse cache is bound HERE and read at ``call.py::ask``, so it never appears in ``GradeFn``
    — see :func:`~promptpotter.judges.call.bind_cache` for why an infrastructure handle stays out
    of the judge protocol. This is also the scope that makes it matter: the stale-data ladder
    re-enters ``measure_sample`` twice more per degraded sample, and each re-entry lands here.
    """
    from promptpotter.judges.call import bind_cache

    with bind_cache(cache):
        verdict = await judge.grade(spec, result)
    banked = result.get("pipeline_data")
    if isinstance(banked, dict):
        # Cast because the keys are TERM-named, so `PipelineData` cannot declare them — the same
        # reason a connector's observation keys (`env_reward`) are written through a plain dict.
        pd = cast("dict[str, Any]", banked)
        if verdict.label:
            pd[f"{term}_label"] = verdict.label
        if detail := (verdict.error or verdict.explanation):
            pd[f"{term}_why"] = detail
    return verdict.score


def build_evaluators(
    specs: Mapping[str, JudgeSpec], *, cache: LLMReuseCache | None = None
) -> tuple[Evaluator, ...]:
    """A campaign's judges, as ordinary ``per_sample`` evaluators — one per TERM.

    **The mapping is keyed by the term the scoring formula reads, never by the judge's name**, and
    that is what makes a multi-STEP schema expressible at all: `retrieve → ground → answer` is
    three entries whose graders are three different rubrics, and one keyed by judge would collapse
    any two sharing a rubric into a single term. It also keeps the operator's vocabulary in one
    place — the formula names `answer_correct`, so the config declares `answer_correct`, rather
    than naming a registry key here and a derived term there.

    Declaration ORDER is the step order and is preserved (a YAML mapping loads ordered, and so does
    the dict it becomes). Nothing reads it yet; a later per-step difficulty model does, off the
    banked terms rather than off a re-measure — [`docs/methods/verdict-resolution.md`] § Phase 3.

    Every term is validated against the same rules a package evaluator obeys, which is what
    :func:`~promptpotter.application.scoring.evaluators.validate_campaign_evaluator` exists for:
    the name an OPERATOR picks is the one that can collide with something, and both collisions —
    a term `cell_namespace` binds itself, a term a package evaluator already owns — are silent.

    ``cache`` is ``Stores.judge_reuse``, shared by every term: it is keyed on the rendered prompt,
    so two judges cannot read each other's replies and a repeated grading of one comparison is
    bought once. It defaults to ``None`` so a test or a one-off construction re-samples rather than
    reaching a store it was never given; every real caller passes one, and there is exactly one —
    ``initialization/loop_start.py::populate_session_scoring``, which the runner and the four
    diagnostic verbs both arm through.
    """
    from promptpotter.application.scoring.evaluators import (
        Evaluator,
        validate_campaign_evaluator,
    )

    out: list[Evaluator] = []
    for term, spec in specs.items():
        if not term.isidentifier():
            raise ValueError(
                f"judge term {term!r}: a scoring formula reaches a term by NAME, and the compiler's "
                f"AST allowlist resolves a bare name only — so a term that is not a Python "
                f"identifier materializes a value no formula can address."
            )
        judge = get(spec.name)
        ev = Evaluator(
            name=term,
            description=judge.description,
            scope="per_sample",
            # `partial` binds the term and the campaign's spec the same way `_compute_recall` is
            # bound to its candidate key — a parameterized evaluator is an existing shape here.
            compute=partial(_compute, judge=judge, spec=spec, term=term, cache=cache),
            # A judge comparing against a gold is UNDEFINED on a verifier-graded bank rather than
            # zero there. `materialize_sample_values` reads this and skips.
            needs_labels=judge.needs_gold,
        )
        validate_campaign_evaluator(ev, f"campaign judge {spec.name!r}")
        out.append(ev)
    return tuple(out)
