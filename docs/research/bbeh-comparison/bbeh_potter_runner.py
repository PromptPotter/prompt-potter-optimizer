"""BBEH x PromptPotter harness.

Kept out of ``shared_config.py`` because the CAPO/DSPy notebooks live on
Colab and must stay import-safe there (no ``promptpotter.*`` imports).
"""

import contextlib
from pathlib import Path
from typing import Any

from shared_config import MODEL_ID, Record, export_results

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.embedded_run import (
    mint_and_score_origin,
    open_session,
    run_campaign,
)
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.scoring.formula import SCORING_FUNCTIONS
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.sample import Sample
from promptpotter.presentation.views.completion import report_completion
from promptpotter.presentation.views.display import set_display_tags
from promptpotter.presentation.views.live.display import LiveDisplay

# datasets/bbeh/ is the SoT for everything about the task — pipeline.yaml drives the
# target-layer schema (read by open_session via dataset_name="bbeh"), campaign.yaml
# carries every loop-control knob and the optimizer LLM, task_description.md the framing.
_BBEH_CAMPAIGN_YAML = Path(__file__).resolve().parents[3] / "datasets" / "bbeh" / "campaign.yaml"


def _normalize(examples: list[Record]) -> list[Sample]:
    return samples_from_dicts(
        [{"query": ex["input"], "ground_truth": ex["target"]} for ex in examples]
    )


def build_campaign_config(
    *,
    max_rounds: int | None = None,
    n_variants: int | None = None,
    sp_budget_ttest: int | None = None,
) -> CampaignConfig:
    """Load ``datasets/bbeh/campaign.yaml`` and merge any non-None overrides on top.

    Overrides are ad-hoc notebook conveniences; the file stays the project default and the SoT
    for CLI runs. The optimizer LLM is install-global
    (``promptpotter/assets/optimizer/pipeline.yaml``) — edit that to change the optimizer
    model/provider, not the campaign config.
    """
    # Rasch-validation run scaffolding (git log): a large l1_patience defers L2/L3 firing for the
    # run window so the per-round adaptive queue accumulates δ evidence.
    optimization: dict[str, Any] = {"max_rounds": 5, "l1_patience": 99}
    optimization.update(
        {k: v for k, v in {"max_rounds": max_rounds, "n_variants": n_variants}.items() if v}
    )
    overrides: dict[str, Any] = {"optimization": optimization}
    if sp_budget_ttest is not None:
        overrides["sp_budget_ttest"] = sp_budget_ttest
    return load_dataset_campaign_config(_BBEH_CAMPAIGN_YAML, overrides=overrides)


async def run_bbeh_campaign(
    train_pool: list[Record],
    test_by_task: dict[str, list[Record]],
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

    session = await open_session("bbeh", backend_url=backend_url, on_status=print)
    try:
        campaign_config = build_campaign_config(
            max_rounds=max_rounds,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=print)
        set_display_tags(session.pipeline_schema)

        observers, dataset_obj, origin = await mint_and_score_origin(
            session,
            train_norm,
            campaign_config,
            pipeline_params=pipeline_params,
            display=LiveDisplay.for_campaign(session, campaign_config),
            on_status=print,
        )
        origin_train_acc = origin.report.accuracy

        cycle_result = await run_campaign(
            observers,
            dataset_obj,
            origin,
            campaign_config,
            session=session,
        )
        report_completion(cycle_result, session=session)
        # Ask the outcome table, never a hand-authored string: the export below is only
        # meaningful for a run that finished, and every halted/failed/paused reason must skip it.
        if stop_reason_outcome(cycle_result.stop_reason) is not StopOutcome.SUCCESS:
            return None

        # The winner comes off the ARTIFACT, not off `CycleResult.winner_prompt_fields`: that one
        # is the wire-side projection and carries a rendered `few_shot_block`, which
        # `PromptTemplate` rejects outright (`extra="forbid"`) — a crash that waits for the first
        # winner with demonstrations and lands after the whole campaign is paid for.
        export = session.store.campaigns.read_export(session.hop)
        winner_pipeline_params = cycle_result.winner_pipeline_params
        train_acc = cycle_result.best_accuracy

        print(f"\n{'=' * 60}")
        print("PER-TASK TEST EVALUATION")
        print("=" * 60)

        per_task_results: dict[str, Record] = {}
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

        winner_prompt_str = export.template().render() if export is not None else ""
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
