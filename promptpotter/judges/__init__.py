from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.scoring.evaluators import Evaluator, validate_campaign_evaluator
from promptpotter.judges.call import absent, bind_cache
from promptpotter.judges.grounding import ANSWER_GROUNDING, EVIDENCE_RETRIEVAL
from promptpotter.judges.protocol import Judge, JudgeSpec
from promptpotter.judges.simpleqa import SEALQA, SIMPLEQA
from promptpotter.shared.errors import CellSendRefusedError
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.plugin_registry import load_registry, lookup

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.application.scoring.evaluators import Evaluator
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.stores import LLMReuseCache

__all__ = [
    "ENTRY_POINT_GROUP",
    "build_evaluators",
    "get",
    "judge_instrument",
    "judge_origins",
    "registered",
]
# The protocol TYPES are deliberately absent: import them from `promptpotter.judges.protocol`,
# the same rule `infrastructure/store/__init__.py` holds. Re-exporting them here would give every
# type two import paths and make this module's surface the registry's plus the protocol's.

ENTRY_POINT_GROUP = "promptpotter.judges"
"""Published: a third party ships a judge by declaring this group and touches nothing here.
Renaming it un-registers every plugin at once."""

_BUILTIN: tuple[Judge, ...] = (SIMPLEQA, SEALQA, EVIDENCE_RETRIEVAL, ANSWER_GROUNDING)


def _validate(j: object, origin: str) -> Judge:
    if not isinstance(j, Judge):
        raise TypeError(f"[{origin}] resolved to {type(j).__name__}, not a Judge.")
    where = f"judge {j.name!r} [{origin}]"
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


@functools.cache
def _load() -> tuple[Mapping[str, Judge], Mapping[str, str]]:
    return load_registry(ENTRY_POINT_GROUP, ((__name__, j) for j in _BUILTIN), _validate)


def registered() -> Mapping[str, Judge]:
    return _load()[0]


def judge_origins() -> Mapping[str, str]:
    """The audit surface — a plugin's name is not greppable in this tree, so the distribution
    behind every registered key is recorded, ours included."""
    return _load()[1]


def get(name: str) -> Judge:
    return lookup(ENTRY_POINT_GROUP, _load(), name)


def judge_instrument(specs: Mapping[str, JudgeSpec]) -> str | None:
    """What a campaign's graders ARE, over every term — the one digest both the measurement key and
    the scorer id fold, ``None`` where it declares none. Sorted, since declaration order is not."""
    if not specs:
        return None
    return stable_hash(
        [[term, get(spec.name).fingerprint(spec)] for term, spec in sorted(specs.items())]
    )


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

    try:
        with bind_cache(cache):
            verdict = await judge.grade(spec, result)
    except (KeyboardInterrupt, asyncio.CancelledError, CellSendRefusedError):
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
