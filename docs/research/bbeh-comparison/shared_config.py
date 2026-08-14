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


# ── Dataset loading & splitting ────────────────────────────────────
def load_and_split() -> tuple[list[Record], dict[str, list[Record]]]:
    """Load full BBEH and split by HF `mini` flag.

    Train pool = all mini examples (460 total), pooled across tasks — used as
    a single flat list for global prompt optimization.
    Test set = all non-mini examples (~4,060 total), grouped by task for the
    per-task accuracy breakdown in results.json.

    The mini/non-mini partition is disjoint by construction (HF flags each
    example), so there is zero leakage between train and test.

    Returns (train_pool, test_by_task):
      train_pool: list[dict] with keys "input", "target", "task"
      test_by_task: dict[str, list[dict]] mapping task -> list of same shape
    """
    from datasets import load_dataset

    ds = load_dataset(HF_DATASET)["train"]

    train_pool: list[Record] = []
    test_by_task: dict[str, list[Record]] = {}

    for ex in ds:
        record = {"input": ex["input"], "target": ex["target"], "task": ex["task"]}
        if ex["mini"] == 1:
            train_pool.append(record)
        else:
            test_by_task.setdefault(ex["task"], []).append(record)

    # Deterministic ordering
    import random

    rng = random.Random(SPLIT_SEED)
    rng.shuffle(train_pool)
    test_by_task = {k: test_by_task[k] for k in sorted(test_by_task.keys())}

    return train_pool, test_by_task


# ── Result export ──────────────────────────────────────────────────
def export_results(
    method: str,
    per_task: dict[str, Record],
    config: Record,
    optimized_prompts: dict[str, str],
    output_path: str = "results.json",
) -> Record:
    """Write standardized JSON results file."""
    import json
    from datetime import datetime

    accuracies = [t["accuracy"] for t in per_task.values()]
    overall = sum(accuracies) / len(accuracies) if accuracies else 0.0

    result = {
        "method": method,
        "model": MODEL_ID,
        "dataset": "bbeh-full (train=mini 460, test=non-mini ~4060)",
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
