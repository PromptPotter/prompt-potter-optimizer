"""UserPromptSubmit hook — nudge the potter-dev LEARN loop on a correction.

Reads the hook JSON from stdin. When the operator's prompt looks like a
correction of a prior behavior, prints a small `additionalContext` payload so
Claude records the lesson into `.claude/skills/potter-dev/rules.md` after
resolving it. No match (or any parse problem) → print nothing, exit 0. The hook
never blocks a prompt; a missed signal just means the operator invokes
potter-dev by hand.
"""

from __future__ import annotations

import json
import re
import sys

# Phrases that strongly signal "you did the wrong thing" — kept targeted so
# ordinary requests don't trip it. Matching is case-insensitive.
_CORRECTION_PATTERNS: tuple[str, ...] = (
    r"\bno,?\s+(don'?t|do not|that'?s|stop|not\b)",
    r"\bthat'?s (wrong|not right|not correct|incorrect|not what)",
    r"\byou keep\b",
    r"\byou (always|keep) (do|doing)\b",
    r"\bi (already |just )?told you\b",
    r"\b(please )?(don'?t|do not|never) (ever )?(do|say|use|add|write|edit|delete|trim|change|make)\b",
    r"\bstop (doing|using|adding)\b",
    r"\bwhy did you\b",
    r"\byou (shouldn'?t|should not|weren'?t supposed to|were supposed to)\b",
    r"\bnot what i (asked|wanted|meant|said)\b",
    r"\b(wrong|mistake) again\b",
)

_NUDGE = (
    "This message may be correcting a prior behavior. After you resolve it, "
    "invoke the potter-dev skill in LEARN mode to distill the correction into "
    "one rule and append it to .claude/skills/potter-dev/rules.md so it does "
    "not recur (update an existing rule instead of duplicating)."
)


def _looks_like_correction(prompt: str) -> bool:
    return any(re.search(p, prompt, re.IGNORECASE) for p in _CORRECTION_PATTERNS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not _looks_like_correction(prompt):
        return 0
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _NUDGE,
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
