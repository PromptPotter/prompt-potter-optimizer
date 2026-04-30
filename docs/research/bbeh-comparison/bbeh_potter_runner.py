"""BBEH x PromptPotter harness.

Kept out of ``shared_config.py`` because the CAPO/DSPy notebooks live on
Colab and must stay import-safe there (no ``promptpotter.*`` imports).
"""

import contextlib
from pathlib import Path
from typing import Any

from shared_config import MODEL_ID, export_results

from promptpotter.application.config import (
    CampaignConfig,
    configure_and_apply_pipeline,
    load_campaign_config,
)
from promptpotter.application.scoring.formula import SCORING_FUNCTIONS
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.presentation.views.display import set_display_tags
from promptpotter.presentation.views.notebook_run import (
    init_notebook_session,
    prepare_scoring_context_notebook,
    run_optimization_notebook,
)

BBEH_TASK_DESCRIPTION = (
    "Solve a reasoning problem from BIG-Bench Extra Hard (BBEH), which spans 23 "
    "diverse task types including boardgame QA, multi-step arithmetic, causal "
    "reasoning, disambiguation, and adversarial distractor text. Read the input "
    "carefully, reason step by step as needed, and return only the final answer."
)


def _normalize(examples: list[dict]) -> list[dict]:
    return [
        {"query": ex["input"], "ground_truth": ex["target"], "sample_id": i}
        for i, ex in enumerate(examples)
    ]


def build_campaign_config(
    *, max_rounds: int, n_variants: int, sp_budget_ttest: int
) -> CampaignConfig:
    return load_campaign_config(
        {
            "dataset_name": "bbeh",
            "scoring": "exact_match(predicted, ground_truth)",
            "sp_budget_ttest": sp_budget_ttest,
            "exclude_nodes": [],
            "pipeline_overrides": {},
            "optimization": {
                "l1_patience": 2,
                "max_rounds": max_rounds,
                "n_variants": n_variants,
                "creativity": 0.7,
                "improvement_threshold": 0.01,
                "max_failures": 10,
                "degradation_threshold": 0.4,
                "l2_patience": 5,
                "l3_patience": 3,
                "l2_temperature": 0.3,
                "l3_temperature": 0.5,
                "elimination_n_min": 4,
                "elimination_alpha": 0.2,
            },
            "optimizer_llm": {
                "model": "openai/gpt-oss-120b",
                "provider": "groq",
                "temperature": 0.4,
                "max_tokens": 2000,
            },
        }
    )


async def run_bbeh_campaign(
    train_pool: list[dict],
    test_by_task: dict[str, list[dict]],
    *,
    max_rounds: int,
    n_variants: int,
    sp_budget_ttest: int,
    output_path: Path,
    backend_url: str = "http://127.0.0.1:8000",
) -> dict[str, Any] | None:
    """End-to-end BBEH run: baseline -> optimize -> per-task test eval -> export.

    Returns ``None`` when interrupted (Ctrl+C) — optimization artifacts are
    already saved to disk by the loop's finalizer; the test-eval / export
    phase is skipped because the user signalled stop.
    """
    exact_match = SCORING_FUNCTIONS["exact_match"]
    tasks = sorted(test_by_task.keys())
    train_norm = _normalize(train_pool)
    test_norm_by_task = {t: _normalize(v) for t, v in test_by_task.items()}

    print(f"\n{'=' * 60}")
    print(f"GLOBAL OPTIMIZATION ({len(train_norm)} train samples)")
    print("=" * 60)

    session = await init_notebook_session(backend_url=backend_url, dataset_name="bbeh")
    try:
        campaign_config = build_campaign_config(
            max_rounds=max_rounds,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=print)
        set_display_tags(session.pipeline_schema)

        _, dataset_obj, campaign_rounds, _ = await prepare_scoring_context_notebook(
            session,
            train_norm,
            campaign_config,
            pipeline_params=pipeline_params,
        )
        baseline_train_acc = campaign_rounds[0]["accuracy"] if campaign_rounds else 0.0

        campaign_rounds, cycle_result = await run_optimization_notebook(
            campaign_rounds,
            dataset_obj,
            campaign_config,
            session=session,
            experiment_id="",
            task_context={"task_description": BBEH_TASK_DESCRIPTION},
        )
        if cycle_result is None or cycle_result.stop_reason == "interrupted":
            return None

        winner_prompt_fields = cycle_result.winner_prompt_fields
        winner_pipeline_params = cycle_result.winner_pipeline_params
        train_acc = cycle_result.best_accuracy

        print(f"\n{'=' * 60}")
        print("PER-TASK TEST EVALUATION")
        print("=" * 60)

        per_task_results: dict[str, dict] = {}
        for i, task in enumerate(tasks, start=1):
            test_items = test_norm_by_task[task]
            hits = 0
            for ex in test_items:
                resp = await session.backend_client.run_query(
                    ex["query"], pipeline_params=winner_pipeline_params
                )
                ranking = resp.get("data", {}).get("final_ranking") or []
                predicted = ranking[0].get("candidate", "") if ranking else ""
                hits += int(exact_match(predicted, ex["ground_truth"]))
            acc = hits / len(test_items) if test_items else 0.0
            per_task_results[task] = {"accuracy": round(acc, 4), "n_test": len(test_items)}
            print(f"  [{i:2d}/{len(tasks)}] {task:<40s} {acc:>6.1%}  ({hits}/{len(test_items)})")

        macro_avg = sum(r["accuracy"] for r in per_task_results.values()) / len(per_task_results)
        total_test = sum(r["n_test"] for r in per_task_results.values())
        print(
            f"\nMacro-avg test accuracy: {macro_avg:.1%}  "
            f"(global winner, {total_test} non-mini examples across {len(tasks)} tasks)"
        )

        winner_prompt_str = PromptTemplate(**winner_prompt_fields).render()
        return export_results(
            method="promptpotter",
            per_task=per_task_results,
            config={
                "optimizer": "promptpotter",
                "max_rounds": max_rounds,
                "n_variants": n_variants,
                "sp_budget_ttest": sp_budget_ttest,
                "model_id": MODEL_ID,
                "n_train": len(train_pool),
                "train_accuracy": round(train_acc, 4),
                "baseline_train_accuracy": round(baseline_train_acc, 4),
                "rounds": len(campaign_rounds),
                "methodology": (
                    "Single global prompt optimized on 460 mini-BBEH examples pooled "
                    "across 23 tasks; evaluated on all non-mini examples (~4,060). "
                    "Mini/non-mini partition is disjoint by HF flag - no leakage."
                ),
                "note": "unmeasured starting hyperparameters - pre-sweep",
            },
            optimized_prompts={"__global__": winner_prompt_str},
            output_path=str(output_path),
        )
    finally:
        # Always close the httpx pool — leaked async clients are the most
        # common cause of "kernel stuck connecting" after a notebook Ctrl+C.
        # ``Exception`` excludes BaseException subclasses (KeyboardInterrupt,
        # SystemExit), so a second Ctrl+C still force-quits.
        with contextlib.suppress(Exception):
            await session.backend_client.aclose()
