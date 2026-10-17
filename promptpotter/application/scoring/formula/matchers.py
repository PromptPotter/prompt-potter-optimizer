"""The LABEL arm only: the backend destructures ``answer_field`` first, outside this repo."""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable
from typing import Annotated, Any

from promptpotter.shared.answer_text import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    extract_last_boxed,
    extract_plan_block,
    extract_solution,
    text_list_rank,
)
from promptpotter.shared.hashing import shapes_optimizer_prompt


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = extract_gsm8k_number(ground_truth)
    pred = extract_gsm8k_number(predicted)
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    pred_num = extract_boxed_number(predicted)
    if pred_num is None:
        return 0.0
    try:
        pred = int(pred_num)
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


def _list_rr(predicted: str, ground_truth: str) -> float:
    rank = text_list_rank(predicted, ground_truth)
    return 1.0 / rank if rank else 0.0


def _label_match(predicted: str, ground_truth: str) -> float:
    """The tolerances are google-deepmind/bbeh ``evaluate.py``'s (``preprocess_sample`` + ``fuzzy_match``)."""
    p = extract_last_bold(predicted).strip().strip("_").strip().lower()
    p = p.replace(", ", ",").replace("**", "").split("\n")[0].removesuffix(".")
    if p.startswith("$") and p.endswith("$"):
        p = p[1:-1]
    for wrapper in ("boxed{", "text{", "texttt{"):
        if wrapper in p and p.endswith("}"):
            p = p[:-1].split(wrapper)[1]
    g = extract_last_bold(ground_truth).strip().lower().replace(", ", ",")
    if p == g:
        return 1.0
    if len(p) == 3 and p[0] == "(" and p[-1] == ")":
        return 1.0 if p[1] == g else 0.0
    if len(g) == 3 and g[0] == "(" and g[-1] == ")":
        return 1.0 if g[1] == p else 0.0
    try:
        if float(p) == float(g):
            return 1.0
    except ValueError:
        pass
    return (
        1.0
        if p.replace("'", "") == g.replace("'", "")
        or f"[{g}]" == p
        or f"[{p}]" == g
        or (p.endswith("?") and p[:-1] == g)
        else 0.0
    )


_ARTICLES_RE = re.compile(r"\b(a|an|the)\b")
# Group 1 is a point or a dash between digits, or a sign or point opening a number: kept.
_PUNCTUATION_RE = re.compile(
    rf"((?<=\d)[.\-](?=\d)|(?<![\w.])[.\-](?=\.?\d))|[{re.escape(string.punctuation)}]"
)


def _squad_normalize(text: str) -> str:
    """SQuAD's ``normalize_answer``, but a number keeps its point and sign: stripped, 1.3 is 13."""
    kept = _PUNCTUATION_RE.sub(lambda m: m.group(1) or "", text.lower())
    return " ".join(_ARTICLES_RE.sub(" ", kept).split())


def _em_match(predicted: str, ground_truth: str) -> float:
    """SQuAD's ``normalize_answer`` on both sides, read off the span ``label_match`` reads; a reply
    with no span misses, where normalized it would equal a gold that is only an article."""
    span = extract_last_bold(predicted)
    if not span:
        return 0.0
    return 1.0 if _squad_normalize(span) == _squad_normalize(ground_truth) else 0.0


_MATH_TEXT_RE = re.compile(r"\\(?:text|textbf|mathrm|mbox)\{([^{}]*)\}")
_MATH_REWRITES = (
    ("\\dfrac", "\\frac"),
    ("\\tfrac", "\\frac"),
    ("\\left", ""),
    ("\\right", ""),
    ("\\!", ""),
    ("\\,", ""),
    ("\\;", ""),
    ("\\ ", ""),
    ("^{\\circ}", ""),
    ("^\\circ", ""),
    ("\\%", ""),
    ("\\$", ""),
    ("$", ""),
)


def _math_normalize(answer: str) -> str:
    """hendrycks/math ``is_equiv``'s string rules, less its decimal-to-fraction rewrite."""
    s = answer.split("\\text{ ")[0]
    for old, new in _MATH_REWRITES:
        s = s.replace(old, new)
    s = _MATH_TEXT_RE.sub(r"\1", s)
    s = "".join(s.split()).rstrip(".")
    s = re.sub(r"\\frac(\d)(\d)", r"\\frac{\1}{\2}", s)
    s = re.sub(r"\\sqrt(\d)", r"\\sqrt{\1}", s)
    if s.count("=") == 1 and len(s.split("=")[0]) <= 2:
        s = s.split("=")[1]
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    ratio = re.fullmatch(r"(-?\d+)/(\d+)", s)
    return f"\\frac{{{ratio.group(1)}}}{{{ratio.group(2)}}}" if ratio else s


def _math_match(predicted: str, ground_truth: str) -> float:
    boxed = extract_last_boxed(predicted)
    p = _math_normalize(boxed if boxed is not None else extract_last_bold(predicted))
    return 1.0 if p == _math_normalize(ground_truth) else 0.0


_CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})(AM|PM)")


def _clock(text: str) -> int:
    m = _CLOCK_RE.fullmatch(text.strip())
    if m is None:
        raise ValueError(text)
    return (int(m[1]) % 12 + (12 if m[3] == "PM" else 0)) * 60 + int(m[2])


def _meetings_met(
    steps: list[str],
    friends: dict[str, tuple[str, int, int, int]],
    place: str,
    clock: int,
    travel: dict[str, dict[str, int]],
) -> int:
    """google-deepmind/natural-plan ``evaluate_meeting_planning.py::validator_from_text``: valid meetings before the first fault."""
    met: set[str] = set()
    for step in steps:
        try:
            if step.startswith("You start"):
                continue
            if step.startswith("You travel"):
                destination = step.split("travel to ")[1].split(" in")[0].strip()
                clock += travel[place][destination]
                place = destination
            elif step.startswith("You wait"):
                until = _clock(step.split("wait until ")[1].split(".")[0])
                if until <= clock:
                    raise ValueError(step)
                clock = until
            elif step.startswith("You meet"):
                person = step.split("meet ")[1].split(" for")[0].strip()
                where, opens, closes, minutes = friends[person]
                if person in met or place != where or clock < opens or clock + minutes > closes:
                    raise ValueError(step)
                met.add(person)
                clock += minutes
            else:
                raise ValueError(step)
        except (KeyError, IndexError, ValueError):
            break
    return len(met)


def _meeting_plan_match(predicted: str, ground_truth: str) -> float:
    gold = json.loads(ground_truth)
    (place, at), *constraints = gold["constraints"]
    friends = {
        name: (where, _clock(span.split("to")[0]), _clock(span.split("to")[1]), minutes)
        for name, where, span, minutes in constraints
    }
    start = _clock(at)
    steps = [step.strip() for step in extract_solution(predicted).split(".") if step.strip()]
    met = _meetings_met(steps, friends, place, start, gold["dist_matrix"])
    best = _meetings_met(gold["golden_plan"], friends, place, start, gold["dist_matrix"])
    return 1.0 if met == best else 0.0


_BLOCKSWORLD_STEP_RE = re.compile(
    r"(pick up|put down|unstack|stack) the (\w+) block(?: (from on top of|on top of) the (\w+) block)?\.?"
)


def _blocksworld_plan_match(predicted: str, ground_truth: str) -> float:
    """PlanBench task 1 as VAL grades it: every step's preconditions hold, then every goal fact."""
    gold = json.loads(ground_truth)
    below: dict[str, str] = dict(gold["below"])
    holding: str | None = None

    def clear(block: str | None) -> bool:
        return block in below and block not in below.values()

    for line in extract_plan_block(predicted).lower().splitlines():
        if not (step := line.strip()):
            continue
        m = _BLOCKSWORLD_STEP_RE.fullmatch(step)
        if m is None:
            return 0.0
        verb, top, preposition, under = m.groups()
        if verb == "pick up":
            ok = (
                preposition is None and holding is None and below.get(top) == "table" and clear(top)
            )
        elif verb == "unstack":
            ok = preposition == "from on top of" and holding is None and below.get(top) == under
            ok = ok and clear(top)
        elif verb == "put down":
            ok = preposition is None and holding == top
        else:
            ok = preposition == "on top of" and holding == top and clear(under)
        if not ok:
            return 0.0
        if verb in ("pick up", "unstack"):
            holding = top
            del below[top]
        else:
            below[top] = under or "table"
            holding = None
    return 1.0 if all(below.get(top) == under for top, under in gold["goal"]) else 0.0


SCORING_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
    "label_match": _label_match,
    "em_match": _em_match,
    "math_match": _math_match,
    "list_rr": _list_rr,
    "meeting_plan_match": _meeting_plan_match,
    "blocksworld_plan_match": _blocksworld_plan_match,
}


# Told to the origin check-in, not gated; a matcher comparing the raw text carries no entry.
EXTRACTION_NOTES: Annotated[dict[str, str], shapes_optimizer_prompt] = {
    "label_match": (
        "Scoring matches the answer after taking the LAST bolded span (the "
        "last **…** run) of the output, lowercased. Commit the final answer on its "
        "own last line wrapped in double asterisks — e.g. **TRUE**. With "
        "chain-of-thought, an unbolded answer leaves the label buried in the "
        "reasoning and scores as a miss; the bold lets scoring isolate it."
    ),
    "em_match": (
        "Scoring takes the LAST bolded span (the last **…** run) of the output and "
        "compares it to the gold answer after SQuAD normalization — lowercased, "
        "punctuation and the articles a/an/the removed, whitespace collapsed. Commit the "
        "final answer on its own last line wrapped in double asterisks, as the shortest "
        "span that answers the question — e.g. **Arthur's Magazine** — a sentence around "
        "it, or any extra word, scores as a miss."
    ),
    "math_match": (
        "Scoring reads the answer from the LAST \\boxed{…} in the output (else the last "
        "bolded span) and compares it to the gold answer after normalising LaTeX spacing, "
        "\\left/\\right, \\dfrac, \\text{} wrappers, degree and dollar signs. Put the final "
        "answer alone in \\boxed{} on the last line, in the simplest exact form the problem "
        "asks for — e.g. \\boxed{\\frac{3}{4}} or \\boxed{44} — no units, no words and no "
        "equals sign inside the box."
    ),
    "aime_match": (
        "Scoring reads the final integer from the last \\boxed{N} (else the last "
        "number in the text). Put the answer in \\boxed{} on the last line — e.g. "
        "\\boxed{42}."
    ),
    "gsm8k_match": (
        "Scoring reads the answer from the '#### N' field (else the last number in "
        "the text). End with the final number on its own line as '#### 42'."
    ),
    "list_rr": (
        "Scoring reads an ORDERED LIST, one item per line, and looks for the held-out "
        "item in it — earlier scores higher. Emit only the list: one item per line, "
        "nothing before or after it, no commentary on the same line. Bullets and '1.' "
        "numbering are stripped, so they neither help nor hurt; prose wrapped around "
        "the list makes its own line an item and pushes the real ones down."
    ),
    "meeting_plan_match": (
        "Scoring reads the itinerary after the LAST 'SOLUTION:' in the output, splits it into "
        "sentences at periods and replays it with the official NaturalPlan checker, which accepts "
        "only 'You start at X at 9:00AM.', 'You travel to Y in N minutes and arrive at 9:20AM.', "
        "'You wait until 10:30AM.' and 'You meet NAME for M minutes from 10:30AM to 12:30PM.' — "
        "travel takes the matrix's minutes, a meeting counts only at the friend's location inside "
        "their window for at least their minimum, each friend once, and the replay stops at the "
        "first sentence that breaks a rule. The answer scores when the friends validly met equal "
        "the gold plan's count, so end with 'SOLUTION:' and the itinerary alone."
    ),
    "blocksworld_plan_match": (
        "Scoring reads the lines between the LAST '[PLAN]' line and '[PLAN END]', one action per "
        "line in PlanBench's wording — 'pick up the red block', 'unstack the red block from on top "
        "of the blue block', 'put down the red block', 'stack the red block on top of the blue "
        "block' — and simulates them from the initial conditions. A line in any other form, or an "
        "action whose preconditions do not hold, scores the plan as a miss; the plan scores a hit "
        "only when every goal fact holds at the end. Finish with '[PLAN]', the actions, then "
        "'[PLAN END]', with nothing else between the markers."
    ),
}


@shapes_optimizer_prompt
def extraction_note_for_scoring(scoring: str) -> str:
    return " ".join(note for name, note in EXTRACTION_NOTES.items() if name in scoring)


__all__ = [
    "EXTRACTION_NOTES",
    "SCORING_FUNCTIONS",
    "extraction_note_for_scoring",
]
