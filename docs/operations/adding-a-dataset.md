# Adding a Dataset

Process for wiring any new dataset under `datasets/{name}/` — public benchmark or private task. **Research before code.**

## 1. Research the canonical protocol — first, always

Before writing the loader, find the **author-recommended train/test split and evaluation protocol** in the published literature:

- **Public benchmarks** — read the dataset card, the parent repo README, the paper's evaluation section, and any associated leaderboard methodology. Look for "evaluation protocol", "splits", "train/test", "held-out", "leaderboard".
- **Private / operator tasks** — ask the operator: *"What slice have you reserved as test, or do you want to cut one now?"* before defining the optimization pool. Don't assume the whole file is fair game.

If the canonical answer isn't obvious in 5 minutes, delegate it to a fresh agent with no project context — that's the fastest path to an uncontaminated read. Template prompt:

> Research the canonical train/test split and evaluation protocol used in the published literature for *<DATASET>* (HuggingFace: *<path>*, paper: *<arxiv URL>*, repo: *<github URL>*).
>
> Specifically:
> - What split, if any, do the authors recommend in the README, paper, or dataset card?
> - What split do published papers actually score against — full set, a subset, a held-out cut?
> - Is there a sister training dataset that papers use, and if so, which one and at what sample size?
> - Cite every claim (URL + section/line).
>
> Report findings only. Do not write code. Do not propose an implementation. Under 400 words.

## 2. Report findings in `dataset.md`

The new `datasets/{name}/dataset.md` must have a **Data** section that quotes the authors' protocol verbatim, with citations (URL + section). State sample counts and any sister training dataset.

## 3. Operator confirms the cut — before any wire

The cut + protocol decision is operator-directed once the canonical protocol is on the table. **Never invent a split.** **Never consume a canonical test set as an optimization pool** without the operator explicitly accepting that the resulting number is not leaderboard-comparable. If we deviate from the canonical protocol (e.g. consume the test set for diagnostic / meta-campaign use), say so explicitly in `dataset.md` with the reason.

## 4. Then wire

Only after steps 1–3: write the loader, the scorer, and the `datasets/{name}/` config tree (`pipeline.yaml`, `campaign.json`, `task_description.md`, `prompts/{node}.yaml`, `dataset.md`). See [`docs/operations/dataset-selection-rationale.md`](dataset-selection-rationale.md) for selection criteria and [`docs/operations/dataset-reasoning-matrix.md`](dataset-reasoning-matrix.md) for per-dataset model defaults once the candidate graduates from shortlist to wired.
