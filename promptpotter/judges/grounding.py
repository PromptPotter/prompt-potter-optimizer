"""The two graders that read a cell's EVIDENCE rather than its answer.

They exist to make the `retrieve → ground → answer` step schema measurable — three named terms per
cell instead of one, so a candidate that fixed the search and still answered wrongly moves a number
instead of scoring identically to one that never searched. Why the schema must be fixed BEFORE any
cell is bought, and what a later per-step difficulty model re-reads from the banked terms rather
than re-measuring, is [`docs/methods/verdict-resolution.md`] § Phase 3.

**These two rubrics are OURS, and that is the difference from ``simpleqa.py``.** That module's text
is OpenAI's and SealQA's, published with the numbers it produced, so it is quoted verbatim down to
its typos. Nothing published grades the two steps below, so what is here is authored — and a judge
whose rubric nobody validated is a measurement, not an authority. Screen it (``seed-screen``,
``noise-floor``) before a campaign is funded on it, and record the reading in the dataset's
``dataset.md`` the same way any other instrument decision is recorded.

Both read what the backend already emitted about what the system DID — ``pipeline_data::turns``
where it has a conversation, else the ``reasoning_trace`` digest, in that preference order and
never as a choice (:func:`_trace`). **No config key for it:** a grader that could be pointed
somewhere else would be one whose input is not part of what the fingerprint says it graded, so
which channel a cell offers is the backend's fact and never a dataset's setting.

**Neither needs a gold, and that is load-bearing rather than convenient.** A verifier-graded
backend declares ``ground_truth: None`` for every cell, so a gold-comparing judge is skipped by
``materialize_sample_values`` — and agent episodes are exactly the cells that HAVE steps worth
grading separately. On such a backend the answer step is the task's own verifier (``env_reward``)
and needs no judge at all, so these two plus that reward are the whole schema.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from promptpotter.judges.protocol import Judge, JudgeSpec, JudgeVerdict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from promptpotter.domain.scoring import QueryMeasurement

__all__ = ["ANSWER_GROUNDING", "EVIDENCE_RETRIEVAL"]


# The trace's budget in the rubric. `<simplify-the-problem>`: length is a quality tax on the
# grader, not only a bill, and a grading whose input is a 40-turn wall is a worse grading. The
# TAIL, matching `connectors/harbor.py::_tail`'s argument — a trace opens with setup identical
# across candidates and closes with what this one actually found.
_TRACE_CAP = 6000

_LETTER_RE = re.compile(r"\b([ABC])\b")


def _render_turns(turns: Sequence[Mapping[str, Any]]) -> str:
    """A turn-structured cell's conversation, as a grader should read it.

    **Three labels, because the rubrics turn on exactly this distinction.** ``thought`` and ``say``
    are the system's own assertions; ``saw`` is what the environment answered, and only that is
    evidence. Flattened into one prose blob — which is what ``reasoning_trace`` is — a grader
    cannot tell a fact the system LOOKED UP from one it stated, so the retrieval rubric's "an
    answer asserted with no evidence does not make a trace SETTLED" has nothing to bite on and the
    grounding rubric cannot separate a claim from its support.

    The step name rides each turn rather than heading a section: a grader reads linearly, and a
    turn that has drifted out of its step's job is exactly what the reading should surface."""
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


def _trace(result: QueryMeasurement) -> str:
    """What this cell DID, tail-capped — the structured conversation where the backend emits one,
    else the prose digest every backend emits.

    Turns FIRST, and the fallback is not a shim: a backend with no turn concept has one channel and
    always will, so this is two different facts read in preference order rather than an old path
    kept alive beside a new one. Tail-biased either way — a trace opens with setup identical across
    candidates and closes with what this one actually found — and now the tail lands on whole turns
    rather than slicing through the middle of one."""
    pd = result.get("pipeline_data") or {}
    turns = pd.get("turns")
    text = (
        _render_turns(turns)
        if isinstance(turns, list) and turns
        else str(pd.get("reasoning_trace") or "")
    ).strip()
    return text[-_TRACE_CAP:] if len(text) > _TRACE_CAP else text


def _parser(letters: dict[str, str]) -> Callable[[str], str | None]:
    """A reply → label reader for a three-way A/B/C taxonomy.

    ``None`` for anything else, which ``call.py::graded`` turns into an ABSENT term. Upstream's
    grader defaults an unparseable reply to its third category; ours never does, for the reason
    ``simpleqa.py`` states — a provider hiccup must not masquerade as a verdict, least of all in
    the direction that flatters the arm under test.
    """

    def parse(reply: str) -> str | None:
        match = _LETTER_RE.search(reply.strip().upper())
        return letters[match.group(1)] if match else None

    return parse


def _build_grade_fn(
    rubric: str,
    judge_name: str,
    *,
    letters: dict[str, str],
    to_score: dict[str, float],
    reads_answer: bool,
) -> object:
    async def grade(spec: JudgeSpec, result: QueryMeasurement) -> JudgeVerdict:
        from promptpotter.judges.call import absent, graded, judge_answer, judge_question

        trace = _trace(result)
        if not trace:
            # ABSENT, never a zero, and never a model call. A backend that emits no trace has not
            # produced a badly-grounded answer — it has produced nothing this judge can read, and
            # scoring that as UNGROUNDED would report "the system never uses evidence" for every
            # cell of a run that simply routed through a backend with no trace channel.
            return absent(
                judge_name,
                "no `reasoning_trace` on this cell — this judge grades the evidence a backend "
                "emitted, so a backend that emits none is unmeasurable here, not ungrounded.",
            )
        answer = judge_answer(result)
        if reads_answer and answer is None:
            # Same rule one axis over: a cell with no answer has not produced an UNGROUNDED one.
            # This arm is load-bearing rather than defensive — `predicted` is the `NO_RESULT`
            # sentinel on every cell of a backend that emits no ranking, so without it the rubric
            # is handed the literal string `NO_RESULT` as the answer and grades it.
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


# The middle score is a PRIOR, and naming it as one is the point. `verdict-resolution.md` § Phase 3
# says a hand-set weighting is "a prior we invented and banked into the gate" — so is 0.5. What
# keeps it recoverable is that `judges/__init__.py::_compute` banks the LABEL beside the score, so
# a later fit re-reads SETTLED/PARTIAL/MISSING off archived rows and derives its own thresholds
# rather than re-measuring under new ones.
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
        # Grades the EVIDENCE, deliberately not the answer — so it still reads on a cell whose
        # answer never arrived, which is the case the retrieve step most wants measured.
        reads_answer=False,
    ),
    labels=tuple(_RETRIEVAL_SCORES),
    to_score=_RETRIEVAL_SCORES,
    # SUFFICIENCY, not correctness — so no gold, and the judge runs on a verifier-graded bank where
    # a gold-comparing one is skipped outright. Two reasons, and the second is why this is the
    # better measurement rather than merely the reachable one. A harbor cell declares
    # `ground_truth: None` (`connectors/harbor.py::_extract_experiment`), so `needs_gold=True`
    # would make this judge dead on the one backend whose cells are turn-structured enough to have
    # steps at all. And handing a grader the gold invites it to accept any trace that merely
    # CONTAINS the gold string; asking whether the evidence settles the question cannot be passed
    # that way, and does not leak the answer into the instrument.
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
    # It compares an answer to the system's OWN trace, so it is defined wherever a trace exists,
    # gold or no gold. Both judges in this module read only what the run produced, which is what
    # makes the pair usable on the agent episodes that have steps to grade in the first place.
    needs_gold=False,
)
