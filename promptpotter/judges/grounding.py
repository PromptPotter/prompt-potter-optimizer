from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from promptpotter.judges.call import absent, graded, judge_answer, judge_question
from promptpotter.judges.protocol import Judge, JudgeSpec, JudgeVerdict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from promptpotter.domain.scoring import MeasuredCell

__all__ = ["ANSWER_GROUNDING", "EVIDENCE_RETRIEVAL"]


# The TAIL is kept: a trace opens with setup identical across candidates.
_TRACE_CAP = 6000

_LETTER_RE = re.compile(r"\b([ABC])\b")


def _render_turns(turns: Sequence[Mapping[str, Any]]) -> str:
    out: list[str] = []
    for turn in turns:
        head = f"[{turn.get('index', '?')}] {turn.get('source') or 'agent'}"
        if step := turn.get("step"):
            head += f" | step={step}"
        if tools := turn.get("tools"):
            head += f" | used {', '.join(str(t) for t in tools)}"
        out.append(head)
        for label, key in (("thought", "reasoning"), ("say", "message"), ("saw", "observation")):
            if text := str(turn.get(key) or "").strip():
                out.append(f"  {label}: {text}")
    return "\n".join(out)


def _trace(result: MeasuredCell) -> str:
    pd = result.pipeline
    text = (_render_turns(pd.turns) if pd.turns else pd.reasoning_trace or "").strip()
    return text[-_TRACE_CAP:] if len(text) > _TRACE_CAP else text


def _parser(letters: dict[str, str]) -> Callable[[str], str | None]:
    def parse(reply: str) -> str | None:
        # The LAST letter: a reply ends on its verdict, and first-match grades "not A, so C" as A.
        found = _LETTER_RE.findall(reply.strip().upper())
        return letters[found[-1]] if found else None

    return parse


def _build_grade_fn(
    rubric: str,
    judge_name: str,
    *,
    letters: dict[str, str],
    to_score: dict[str, float],
    reads_answer: bool,
) -> object:
    async def grade(spec: JudgeSpec, result: MeasuredCell) -> JudgeVerdict:

        trace = _trace(result)
        if not trace:
            return absent(
                judge_name,
                "no `turns` and no `reasoning_trace` on this cell — this judge grades the evidence "
                "a backend emitted, so one that emits none is unmeasurable here, not ungrounded.",
            )
        answer = judge_answer(result)
        if reads_answer and answer is None:
            return absent(
                judge_name,
                "this cell carries no answer text — grading the absence as ungrounded would "
                "score the sentinel, so the term is omitted instead.",
            )
        prompt = rubric.format(
            question=judge_question(result),
            predicted_answer=answer or "",
            trace=trace,
        )
        return await graded(
            spec.stages[0], prompt, judge=judge_name, parse=_parser(letters), to_score=to_score
        )

    return grade


_RETRIEVAL_RUBRIC = """
You are auditing the EVIDENCE a system gathered while working on a question. You are not judging
its answer, and you are deliberately not told what the correct answer is.

Judge exactly one thing: does the trace contain evidence that SETTLES the question — enough that a
careful reader of the trace alone could answer it and know they were right?

Grade as one of:
A: SETTLED — the trace contains evidence sufficient to answer the question. The system need not
   have drawn the conclusion, and the evidence need not be quoted; it is enough that the decisive
   fact is there.
B: PARTIAL — the trace contains evidence bearing on the question but not enough to settle it.
   Sources that are relevant yet silent on the decisive fact belong here, and so do sources that
   conflict with nothing in the trace to adjudicate between them.
C: MISSING — nothing in the trace bears on the question. An empty or failed search, results about
   a different entity, and a trace that only restates the question belong here.

Two things are not this grade's business. An answer asserted with no evidence behind it does not
make a trace SETTLED, however confident and however plausible. And a trace that settled the
question and was then answered wrongly is still SETTLED.

Question: {question}
Trace:
{trace}

Just return the letter "A", "B", or "C", with no text around it.
""".strip()

_GROUNDING_RUBRIC = """
You are auditing whether an answer is SUPPORTED by the evidence the system itself gathered. You are
not judging whether the answer is correct — you are not told what the correct answer is, and a
well-grounded answer can still be wrong.

Grade as one of:
A: GROUNDED — every load-bearing claim in the answer traces to something in the trace.
B: PARTIAL — the answer's central claim traces to the trace, but the answer adds specifics the
   trace does not show: a date, a figure, or a name appearing nowhere in it.
C: UNGROUNDED — the answer asserts what the trace does not show, or contradicts it. An answer
   recalled from the system's own knowledge with no support in the trace belongs here, however
   plausible it is.

A refusal or an explicit "I don't know" is GROUNDED when the trace indeed shows nothing to answer
from, and UNGROUNDED when the trace shows the answer and the system declined to use it.

Question: {question}
Answer: {predicted_answer}
Trace:
{trace}

Just return the letter "A", "B", or "C", with no text around it.
""".strip()


_RETRIEVAL_SCORES = {"SETTLED": 1.0, "PARTIAL": 0.5, "MISSING": 0.0}
_GROUNDING_SCORES = {"GROUNDED": 1.0, "PARTIAL": 0.5, "UNGROUNDED": 0.0}

_RETRIEVAL_LETTERS = {"A": "SETTLED", "B": "PARTIAL", "C": "MISSING"}
_GROUNDING_LETTERS = {"A": "GROUNDED", "B": "PARTIAL", "C": "UNGROUNDED"}


EVIDENCE_RETRIEVAL = Judge(
    name="evidence_retrieval",
    version="1",
    description=(
        "The RETRIEVE step: does the trace a system produced contain evidence that SETTLES the "
        "question? Graded independently of whether the system then answered, or answered right."
    ),
    rubric=_RETRIEVAL_RUBRIC,
    grade=_build_grade_fn(  # type: ignore[arg-type]
        _RETRIEVAL_RUBRIC,
        "evidence_retrieval",
        letters=_RETRIEVAL_LETTERS,
        to_score=_RETRIEVAL_SCORES,
        reads_answer=False,
    ),
    labels=tuple(_RETRIEVAL_SCORES),
    to_score=_RETRIEVAL_SCORES,
    # A grader handed the gold accepts any trace that merely CONTAINS the gold string.
    needs_gold=False,
)

ANSWER_GROUNDING = Judge(
    name="answer_grounding",
    version="1",
    description=(
        "The GROUND step: is the answer supported by the evidence the system itself gathered? "
        "Needs no gold — a grounded answer can be wrong, and that separation is the measurement."
    ),
    rubric=_GROUNDING_RUBRIC,
    grade=_build_grade_fn(  # type: ignore[arg-type]
        _GROUNDING_RUBRIC,
        "answer_grounding",
        letters=_GROUNDING_LETTERS,
        to_score=_GROUNDING_SCORES,
        reads_answer=True,
    ),
    labels=tuple(_GROUNDING_SCORES),
    to_score=_GROUNDING_SCORES,
    needs_gold=False,
)
