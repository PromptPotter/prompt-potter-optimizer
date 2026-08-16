"""Shared constants for BBEH competitor comparison notebooks.

``bbeh_capo.ipynb`` and ``bbeh_dspy.ipynb`` both import this file, so the dataset splits,
model config and output format are identical across methods. It imports nothing from
``promptpotter`` on purpose — those two notebooks run on Colab, where the package is absent.
"""

from datetime import UTC
from typing import Any

# One BBEH example as the notebooks pass it around: ``input`` / ``target`` / ``task``.
Record = dict[str, Any]

# ── Model ──────────────────────────────────────────────────────────
MODEL_ID = "gpt-oss-120b"
API_BASE = "https://api.groq.com/openai/v1"

# ── Dataset ────────────────────────────────────────────────────────
HF_DATASET = "BBEH/bbeh"
SPLIT_SEED = 42
# BBEH mini ships 20 examples per task. Every method — PromptPotter and every peer optimizer —
# trains on the first 10 and tests on the second 10, so the comparison is on ONE test set.
# The wider non-mini eval (~4,060 rows) is deliberately deferred: it costs a full re-run of every
# peer and buys nothing until there is a release worth spending it on.
TRAIN_PER_TASK = 10
TEST_PER_TASK = 10


# ── Dataset loading & splitting ────────────────────────────────────
def load_and_split() -> tuple[list[Record], dict[str, list[Record]]]:
    """Load BBEH mini and split it per task at ``TRAIN_PER_TASK``.

    Mini only (460 rows, 23 tasks x 20). Each task's rows are shuffled under the SAME
    ``SPLIT_SEED`` permutation, then cut: train = first 10, test = next 10. Train and test are
    disjoint by construction.

    Returns (train_pool, test_by_task):
      train_pool:   the train halves pooled into one flat list, for global prompt optimization
      test_by_task: task -> held-out rows, for the per-task accuracy breakdown in results.json

    The pooling is a PromptPotter-side convenience — the rows are the same ones the per-task
    peers train on, so pooling changes how the prompt is searched, never what it is scored on.
    """
    import random

    from datasets import load_dataset

    by_task: dict[str, list[Record]] = {}
    for ex in load_dataset(HF_DATASET)["train"]:
        if ex["mini"] != 1:
            continue
        record = {"input": ex["input"], "target": ex["target"], "task": ex["task"]}
        by_task.setdefault(ex["task"], []).append(record)

    train_pool: list[Record] = []
    test_by_task: dict[str, list[Record]] = {}
    for task in sorted(by_task):
        shuffled = list(by_task[task])
        random.Random(SPLIT_SEED).shuffle(shuffled)
        train_pool += shuffled[:TRAIN_PER_TASK]
        test_by_task[task] = shuffled[TRAIN_PER_TASK : TRAIN_PER_TASK + TEST_PER_TASK]

    random.Random(SPLIT_SEED).shuffle(train_pool)
    return train_pool, test_by_task


# ── Result export ──────────────────────────────────────────────────
def export_results(
    method: str,
    per_task: dict[str, Record],
    config: Record,
    optimized_prompts: dict[str, str],
    output_path: str = "results.json",
    model: str | None = None,
) -> Record:
    """Write standardized JSON results file.

    ``model`` names the model the run ACTUALLY reached. It is a parameter rather than the
    ``MODEL_ID`` constant because PromptPotter resolves its target from
    ``datasets/bbeh/pipeline.yaml``, not from this file — stamping the constant reported a model
    the run never called on the single most load-bearing field in the results.
    """
    import json
    from datetime import datetime

    accuracies = [t["accuracy"] for t in per_task.values()]
    overall = sum(accuracies) / len(accuracies) if accuracies else 0.0

    result = {
        "method": method,
        "model": model or MODEL_ID,
        "dataset": f"bbeh-mini (train={TRAIN_PER_TASK}/task, test={TEST_PER_TASK}/task)",
        "split_seed": SPLIT_SEED,
        "timestamp": datetime.now(UTC).isoformat(),
        "config": config,
        "per_task": per_task,
        "overall_accuracy": round(overall, 4),
        "optimized_prompts": optimized_prompts,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Results written to {output_path}")
    print(f"Overall accuracy (macro-avg): {overall:.1%}")
    return result
