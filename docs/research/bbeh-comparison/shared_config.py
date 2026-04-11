"""Shared constants for BBEH competitor comparison notebooks.

Both bbeh_capo.ipynb and bbeh_gepa.ipynb import this file to ensure
identical dataset splits, model config, and output format.
"""

# ── Model ──────────────────────────────────────────────────────────
MODEL_ID = "gpt-oss-120b"
API_BASE = "https://api.groq.com/openai/v1"

# ── Dataset ────────────────────────────────────────────────────────
HF_DATASET = "BBEH/bbeh"
SPLIT_SEED = 42
TRAIN_PER_TASK = 10  # optimization set
TEST_PER_TASK = 10   # held-out evaluation

# ── Scoring ────────────────────────────────────────────────────────
def exact_match(expected: str, predicted: str) -> bool:
    """Case-insensitive, whitespace-stripped exact match."""
    return expected.strip().lower() == predicted.strip().lower()


# ── Dataset loading & splitting ────────────────────────────────────
def load_and_split():
    """Load BBEH mini and return (train_by_task, test_by_task) dicts.

    Each dict maps task_name -> list[dict] with keys "input" and "target".
    The split is deterministic (SPLIT_SEED) and identical across notebooks.
    """
    import random
    from datasets import load_dataset

    ds = load_dataset(HF_DATASET)["train"]
    mini = ds.filter(lambda x: x["mini"] == 1)

    # Group by task
    by_task: dict[str, list[dict]] = {}
    for ex in mini:
        task = ex["task"]
        by_task.setdefault(task, []).append({"input": ex["input"], "target": ex["target"]})

    train_by_task: dict[str, list[dict]] = {}
    test_by_task: dict[str, list[dict]] = {}

    for task, examples in sorted(by_task.items()):
        rng = random.Random(SPLIT_SEED)
        shuffled = examples.copy()
        rng.shuffle(shuffled)
        train_by_task[task] = shuffled[:TRAIN_PER_TASK]
        test_by_task[task] = shuffled[TRAIN_PER_TASK : TRAIN_PER_TASK + TEST_PER_TASK]

    return train_by_task, test_by_task


# ── Result export ──────────────────────────────────────────────────
def export_results(
    method: str,
    per_task: dict[str, dict],
    config: dict,
    optimized_prompts: dict[str, str],
    output_path: str = "results.json",
):
    """Write standardized JSON results file."""
    import json
    from datetime import datetime, timezone

    accuracies = [t["accuracy"] for t in per_task.values()]
    overall = sum(accuracies) / len(accuracies) if accuracies else 0.0

    result = {
        "method": method,
        "model": MODEL_ID,
        "dataset": "bbeh-mini",
        "split_seed": SPLIT_SEED,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
