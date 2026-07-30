"""BBEH x PromptPotter harness.

Kept out of ``shared_config.py`` because the CAPO/DSPy notebooks live on
Colab and must stay import-safe there (no ``promptpotter.*`` imports).
"""

import contextlib
import json
from pathlib import Path
from typing import Any

from shared_config import MODEL_ID, export_results

from promptpotter.application.config import (
    CampaignConfig,
    configure_and_apply_pipeline,
    load_campaign_config,
)
from promptpotter.application.datasets import samples_from_dicts
from promptpotter.application.scoring.formula import SCORING_FUNCTIONS
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.sample import Sample
from promptpotter.presentation.views.display import set_display_tags
from promptpotter.presentation.views.notebook_run import (
    init_notebook_session,
    prepare_origin_notebook,
    run_optimization_notebook,
)

BBEH_TASK_DESCRIPTION = (
    "Solve a reasoning problem from BIG-Bench Extra Hard (BBEH), which spans 23 "
    "diverse task types including boardgame QA, multi-step arithmetic, causal "
    "reasoning, disambiguation, and adversarial distractor text. Read the input "
    "carefully, reason step by step as needed, and return only the final answer."
)

# datasets/bbeh/{pipeline,campaign}.json are the SoT — pipeline.yaml drives the
# target-layer schema (read by init_notebook_session via dataset_name="bbeh"),
# campaign.json carries every loop-control knob and the optimizer LLM.
_BBEH_CAMPAIGN_JSON = Path(__file__).resolve().parents[3] / "datasets" / "bbeh" / "campaign.yaml"


def _normalize(examples: list[dict]) -> list[Sample]:
    return samples_from_dicts(
        [{"query": ex["input"], "ground_truth": ex["target"]} for ex in examples]
    )


def build_campaign_config(
    *,
    max_rounds: int | None = None,
    n_variants: int | None = None,
    sp_budget_ttest: int | None = None,
) -> CampaignConfig:
    """Load campaign.json and patch any non-None overrides on top.

    Overrides are intended as ad-hoc notebook conveniences; campaign.json
    stays the project default and the SoT for CLI runs. The optimizer LLM is
    install-global (``promptpotter/assets/optimizer/pipeline.yaml``) — edit that
    file to change the optimizer model/provider, not the campaign config.
    """
    raw = json.loads(_BBEH_CAMPAIGN_JSON.read_text(encoding="utf-8"))
    cfg = raw.get("campaign_config", raw)

    if sp_budget_ttest is not None:
        cfg["sp_budget_ttest"] = sp_budget_ttest

    # Rasch-validation run scaffolding (git log):
    # large l1_patience defers L2/L3 firing for the run window so the
    # per-round adaptive queue mechanism has time to accumulate δ evidence.
    cfg["optimization"] = {
        **cfg.get("optimization", {}),
        "max_rounds": 5,
        "l1_patience": 99,
    }

    opt_overrides = {
        k: v
        for k, v in {"max_rounds": max_rounds, "n_variants": n_variants}.items()
        if v is not None
    }
    if opt_overrides:
        cfg["optimization"] = {**cfg.get("optimization", {}), **opt_overrides}

    return load_campaign_config(cfg)


async def run_bbeh_campaign(
    train_pool: list[dict],
    test_by_task: dict[str, list[dict]],
    *,
    output_path: Path,
    backend_url: str = "http://127.0.0.1:8000",
    max_rounds: int | None = None,
    n_variants: int | None = None,
    sp_budget_ttest: int | None = None,
) -> dict[str, Any] | None:
    """End-to-end BBEH run: origin -> optimize -> per-task test eval -> export.

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

    session = await init_notebook_session(dataset_name="bbeh", backend_url=backend_url)
    try:
        campaign_config = build_campaign_config(
            max_rounds=max_rounds,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=print)
        set_display_tags(session.pipeline_schema)

        observers, dataset_obj, origin = await prepare_origin_notebook(
            session,
            train_norm,
            campaign_config,
            pipeline_params=pipeline_params,
        )
        origin_train_acc = origin.origin_acc

        cycle_result = await run_optimization_notebook(
            observers,
            dataset_obj,
            origin,
            campaign_config,
            session=session,
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
                    ex.query, pipeline_params=winner_pipeline_params
                )
                ranking = resp.get("data", {}).get("final_ranking") or []
                predicted = ranking[0].get("candidate", "") if ranking else ""
                hits += int(exact_match(predicted, ex.ground_truth))
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
        opt_cfg = campaign_config.optimization
        return export_results(
            method="promptpotter",
            per_task=per_task_results,
            config={
                "optimizer": "promptpotter",
                "max_rounds": opt_cfg.max_rounds,
                "n_variants": opt_cfg.n_variants,
                "sp_budget_ttest": campaign_config.sp_budget_ttest,
                "model_id": MODEL_ID,
                "n_train": len(train_pool),
                "train_accuracy": round(train_acc, 4),
                "origin_train_accuracy": round(origin_train_acc, 4),
                "rounds": cycle_result.n_l1_rounds,
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
