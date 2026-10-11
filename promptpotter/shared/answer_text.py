from __future__ import annotations

import re
from typing import Annotated

from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "GSM8K_ANSWER_RE",
    "extract_boxed_number",
    "extract_gsm8k_number",
    "extract_last_bold",
    "text_list_items",
    "text_list_rank",
    "truncate",
]


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    if len(s) <= max_len:
        return s
    cut = s[: max_len - len(ellipsis)].rsplit(" ", 1)[0]
    return (cut if cut else s[: max_len - len(ellipsis)]) + ellipsis


GSM8K_ANSWER_RE = re.compile(r"####\s*(-?[\d,]+\.?\d*)")
NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")
_LIST_ITEM_RE: Annotated[re.Pattern[str], shapes_optimizer_prompt] = re.compile(
    r"^\s*(?:\d+\s*[.)]|[-*•])\s*"
)


@shapes_optimizer_prompt
def text_list_items(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = _LIST_ITEM_RE.sub("", raw.strip()).strip().strip("*_").strip().lower().strip(".")
        if line:
            out.append(line)
    return out


@shapes_optimizer_prompt
def text_list_rank(text: str, item: str) -> int | None:
    want = item.strip().lower().strip(".")
    if not want:
        return None
    items = text_list_items(text)
    return items.index(want) + 1 if want in items else None


def extract_last_bold(text: str) -> str:
    if not text:
        return ""
    matches = _BOLD_RE.findall(text)
    if matches:
        last: str = matches[-1]
        return last.strip()
    return text


def extract_gsm8k_number(text: str) -> float | None:
    m = GSM8K_ANSWER_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    matches = NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None


def extract_boxed_number(text: str) -> float | None:
    boxed = BOXED_RE.findall(text)
    if boxed:
        try:
            return float(boxed[-1].strip().replace(",", ""))
        except ValueError:
            pass
    matches = NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None
