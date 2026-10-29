"""Never the package ``__init__``, which a config imports: a built-in pulls in the LLM clients."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.evaluators import (
    Evaluator,
    JudgedTerm,
    validate_campaign_evaluator,
)
from promptpotter.application.scoring.judges.call import bind_cache
from promptpotter.application.scoring.judges.grounding import ANSWER_GROUNDING, EVIDENCE_RETRIEVAL
from promptpotter.application.scoring.judges.simpleqa import SEALQA, SIMPLEQA
from promptpotter.domain.judges import Judge, JudgeSpec
from promptpotter.domain.scoring import JudgeReading
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.plugin_registry import Registry

if TYPE_CHECKING:
    from promptpotter.domain.scoring import MeasuredCell
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache


ENTRY_POINT_GROUP = "promptpotter.judges"
"""Published: renaming it un-registers every third-party plugin at once."""

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


JUDGES = Registry(ENTRY_POINT_GROUP, lambda: ((__name__, j) for j in _BUILTIN), _validate)


def judge_instrument(specs: Mapping[str, JudgeSpec]) -> str | None:
    if not specs:
        return None
    return stable_hash(
        [[term, JUDGES.get(spec.name).fingerprint(spec)] for term, spec in sorted(specs.items())]
    )


async def _compute(
    *,
    result: MeasuredCell,
    judge: Judge,
    spec: JudgeSpec,
    cache: LLMReuseCache | None = None,
    **_: Any,
) -> JudgedTerm:
    with bind_cache(cache):
        verdict = await judge.grade(spec, result)
    return JudgedTerm(
        verdict.score,
        JudgeReading(
            label=verdict.label or None, why=(verdict.error or verdict.explanation) or None
        ),
    )


def build_evaluators(
    specs: Mapping[str, JudgeSpec], *, cache: LLMReuseCache | None = None
) -> tuple[Evaluator, ...]:
    out: list[Evaluator] = []
    for term, spec in specs.items():
        if not term.isidentifier():
            raise ValueError(
                f"judge term {term!r}: a scoring formula reaches a term by NAME, and the compiler's "
                f"AST allowlist resolves a bare name only — so a term that is not a Python "
                f"identifier materializes a value no formula can address."
            )
        judge = JUDGES.get(spec.name)
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
            compute=partial(_compute, judge=judge, spec=spec, cache=cache),
            # Undefined, not zero, on a verifier-graded bank: `materialize_sample_values` skips it.
            needs_labels=judge.needs_gold,
        )
        validate_campaign_evaluator(ev, f"campaign judge {spec.name!r}")
        out.append(ev)
    return tuple(out)
