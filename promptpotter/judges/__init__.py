from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from functools import partial
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, cast

from promptpotter.judges.grounding import ANSWER_GROUNDING, EVIDENCE_RETRIEVAL
from promptpotter.judges.protocol import Judge, JudgeSpec
from promptpotter.judges.simpleqa import SEALQA, SIMPLEQA

logger = logging.getLogger(__name__)

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
    """The ``Evaluator.compute`` a judge becomes. ``measure_sample`` banks ``pipeline_data`` after
    this returns, so the label and the reason written here reach the archive and the round file."""
    from promptpotter.judges.call import absent, bind_cache

    try:
        with bind_cache(cache):
            verdict = await judge.grade(spec, result)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        # A judge that RAISES must not cost the cell it was grading. Uncaught, this reaches
        # `measure_sample`'s catch-all, which banks `pipeline_data=None` and throws away a backend
        # answer already paid for — exactly what `ask` never raising exists to prevent, undone one
        # frame above it by any OTHER failure inside `grade`: a rubric placeholder the caller does
        # not fill, a label outside `to_score`, a third-party judge's own bug.
        logger.warning("judge %s failed to grade term %s: %s", judge.name, term, exc)
        verdict = absent(judge.name, f"{type(exc).__name__}: {exc}")
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
    """A campaign's judges, as ordinary ``per_sample`` evaluators. ``specs`` is keyed by the term
    the scoring formula reads, never by the judge's name."""
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
        if judge.max_stages is not None and len(spec.stages) > judge.max_stages:
            raise ValueError(
                f"judge term {term!r}: {spec.name!r} asks {judge.max_stages} of the "
                f"{len(spec.stages)} stages declared. `fingerprint` hashes the whole chain, so a "
                f"stage nothing reads re-cuts every archive key and re-pays for every row while "
                f"changing no verdict. Drop it, or declare a judge that asks it."
            )
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
