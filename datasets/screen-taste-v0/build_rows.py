"""Cut `cache.json` from one person's list of films and series they like.

    python datasets/screen-taste-v0/build_rows.py

Each row is one DRAW: ten titles the person likes go in as context, and a SLATE of ten
candidates goes in beside them — one more of theirs, held out, plus nine distractors. The
model returns the slate in its own order; `list_rr` scores where the held-out title landed.

The slate is what makes the metric able to move. Asking for free recall instead scored 0.000
on every cell, because naming one exact title out of all cinema is something a good
recommender fails too. A slate also gives random a known value to beat — mean reciprocal rank
over ten shuffled candidates is H(10)/10 ≈ 0.293, so anything at or below that read the person
no better than chance did.

The pools are `titles.txt` (theirs) and `distractors.txt` (not theirs), one title per line.
Replace either and re-run to re-cut — and when you do, **either copy the directory to a new
`screen-taste-vN` or delete what this name already measured**: a `sample_id` is only unique
within a dataset name, so re-cutting rows under a name that has already run serves the old
measurement for the new row, silently (`datasets/CLAUDE.md`). Deleting is the cheaper half
while the measurements are worth less than the fork.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
N_ROWS = 20
N_SHOWN = 10
N_SLATE = 10
# Fixed so a re-run reproduces the same cut; bump only with a new dataset name.
SEED = 20260829

QUERY = """Titles this person likes:
{shown}

Candidates:
{slate}"""


def _pool(name: str) -> list[str]:
    return [
        line.strip()
        for line in (HERE / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def build() -> dict:
    liked = _pool("titles.txt")
    other = _pool("distractors.txt")

    # A title on both lists is an unscoreable row: it is the answer AND a wrong answer, and
    # nothing downstream would say so — the cell would just read as a miss the model earned.
    both = sorted(set(liked) & set(other))
    if both:
        raise SystemExit(f"in titles.txt AND distractors.txt, so no row can score: {both}")
    need = N_SHOWN + 1
    if len(liked) < need + 4:
        raise SystemExit(
            f"titles.txt has {len(liked)} titles; a draw needs {need} and the rows go stale "
            f"if the pool barely exceeds one draw. Give it at least {need + 4}."
        )
    if len(other) < N_SLATE - 1:
        raise SystemExit(f"distractors.txt has {len(other)}; a slate needs {N_SLATE - 1}.")

    rng = random.Random(SEED)
    items, seen = [], set()
    while len(items) < N_ROWS:
        draw = rng.sample(liked, need)
        shown, held = draw[:N_SHOWN], draw[N_SHOWN]
        # The ANSWER is what makes a row distinct; two draws holding out the same title
        # teach the same lesson twice and one of them is wasted spend.
        if held in seen:
            continue
        seen.add(held)
        slate = [held, *rng.sample(other, N_SLATE - 1)]
        # Shuffled, or the answer is always first and position alone scores 1.0.
        rng.shuffle(slate)
        items.append(
            {
                "id": len(items),
                "query": QUERY.format(shown="\n".join(shown), slate="\n".join(slate)),
                "ground_truth": held,
            }
        )

    return {
        "name": "screen-taste-v0",
        "created_at": "2026-08-29T00:00:00+00:00",
        "source_file": "titles.txt + distractors.txt — one person's own list, hand-kept",
        "row_count": len(items),
        "items": items,
    }


if __name__ == "__main__":
    bank = build()
    (HERE / "cache.json").write_text(json.dumps(bank, indent=2) + "\n", encoding="utf-8")
    held = {i["ground_truth"] for i in bank["items"]}
    chance = sum(1 / k for k in range(1, N_SLATE + 1)) / N_SLATE
    print(f"cache.json: {bank['row_count']} rows, {len(held)} distinct held-out titles")
    print(f"chance mean RR over a {N_SLATE}-candidate slate: {chance:.4f} — the number to beat")
