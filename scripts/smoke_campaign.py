"""Smoke-test a dataset end-to-end: one L1 round, minimal budget.

Runs the real `init_services → prepare_scoring_context → run_optimization_notebook`
path against any dataset registered in ``DATASET_LOADERS``. Designed for first-
contact verification of a newly scaffolded `datasets/{name}/` bring-up:

- dataset loader present in the registry
- static `pipeline.json` schema parses and resolves
- backend is reachable
- L1 generate / score / critique loop runs end-to-end
- per-node pipeline routing works when only one node is active

Typical usage::

    python scripts/smoke_campaign.py --dataset bbeh
    python scripts/smoke_campaign.py --dataset aime_2025 --samples 3 --variants 2

~90 seconds on `gpt-oss-120b` via Groq. Skips held-out evaluation — the
round trace already proves the pipeline is wired.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Force utf-8 stdout so Windows cp1252 console doesn't choke on box-drawing
# characters emitted by NotebookDisplay.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from promptpotter.application.datasets.builder import DATASET_LOADERS  # noqa: E402
from promptpotter.presentation.ui.campaign import (  # noqa: E402
    configure_pipeline,
    init_services,
    prepare_scoring_context,
    run_optimization_notebook,
)
from promptpotter.shared.errors import ActiveSessionMismatchError  # noqa: E402


def _build_config(
    dataset: str,
    *,
    samples: int,
    variants: int,
    rounds: int,
    patience: int,
    task_context: str,
) -> dict:
    return {
        "dataset_name": dataset,
        "scoring": _infer_scoring(dataset),
        "sp_budget_ttest": samples,
        "exclude_nodes": [],
        "pipeline_overrides": {},
        "task_context": {"task_description": task_context},
        "optimization": {
            "l1_patience": patience,
            "max_rounds": rounds,
            "n_variants": variants,
            "creativity": 0.7,
            "improvement_threshold": 0.01,
            "seed": 42,
            "max_failures": 5,
            "degradation_threshold": 0.4,
            "enable_l2": False,
            "enable_l3": False,
            "l2_patience": 1,
            "l3_patience": 1,
            "l2_temperature": 0.3,
            "l3_temperature": 0.5,
        },
        "optimizer_llm": {
            "model": "openai/gpt-oss-120b",
            "provider": "groq",
            "temperature": 0.4,
            "max_tokens": 2000,
        },
        "pipeline_params": None,
    }


def _infer_scoring(dataset: str) -> str:
    """Prefer the dataset's own campaign.json formula; fall back to exact_match."""
    import json

    cfg_path = _REPO_ROOT / "datasets" / dataset / "campaign.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            formula = cfg.get("campaign_config", {}).get("scoring")
            if formula:
                return formula
        except Exception:
            pass
    return "exact_match(predicted, ground_truth)"


async def _run(args: argparse.Namespace) -> int:
    if args.dataset not in DATASET_LOADERS:
        known = ", ".join(sorted(DATASET_LOADERS)) or "(none)"
        print(
            f"[smoke] ERROR: dataset {args.dataset!r} not registered in DATASET_LOADERS. "
            f"Known: {known}",
            file=sys.stderr,
        )
        return 2

    if not os.environ.get("GROQ_API_KEY"):
        print("[smoke] ERROR: GROQ_API_KEY missing from environment", file=sys.stderr)
        return 2

    print(
        f"[smoke] dataset={args.dataset} samples={args.samples} "
        f"variants={args.variants} rounds={args.rounds}",
        flush=True,
    )
    project_dir = _REPO_ROOT / ".promptpotter" / "projects" / args.dataset
    print(f"[smoke] project dir:  {project_dir}", flush=True)
    print(f"[smoke] campaigns:    {project_dir / 'campaigns'}", flush=True)
    print(f"[smoke] dataset runs: {project_dir / 'dataset_runs'}", flush=True)

    try:
        session = await init_services(
            backend_url=args.backend_url,
            backend_id=args.dataset,
            dataset_name=args.dataset,
            take_over=args.take_over,
        )
    except ActiveSessionMismatchError as exc:
        print(f"[smoke] ERROR: {exc}", file=sys.stderr)
        print(
            "[smoke] Rerun with --take-over to clear the pointer and proceed.",
            file=sys.stderr,
        )
        return 5

    schema = session.pipeline_schema
    if schema is None or not schema.available_models:
        print(
            f"[smoke] ERROR: pipeline schema for {args.dataset!r} declares no "
            f"available_models. Add an `available_models: [...]` list to "
            f"datasets/{args.dataset}/pipeline.json so the L1 validation rail "
            f"can reject candidates that propose unsupported models.",
            file=sys.stderr,
        )
        return 4

    campaign_config = _build_config(
        args.dataset,
        samples=args.samples,
        variants=args.variants,
        rounds=args.rounds,
        patience=args.patience,
        task_context=args.task_context,
    )
    pipeline_params = configure_pipeline(session, campaign_config)

    train_slice = (session.queries or [])[: args.samples]
    if not train_slice:
        print("[smoke] ERROR: dataset loader returned no items", file=sys.stderr)
        return 3

    _baseline_sp, dataset_obj, campaign_rounds, _ = await prepare_scoring_context(
        session,
        train_slice,
        campaign_config,
        pipeline_params=pipeline_params,
    )

    campaign_rounds, result = await run_optimization_notebook(
        campaign_rounds,
        dataset_obj,
        campaign_config,
        session=session,
    )

    cycle_id = getattr(result, "cycle_id", "") or ""
    cycle_dir = (
        _REPO_ROOT / ".promptpotter" / "projects" / args.dataset / "campaigns" / cycle_id
        if cycle_id
        else None
    )

    if not campaign_rounds:
        print(
            f"\n[smoke] dataset={args.dataset} "
            f"rounds=0 "
            f"cycle={cycle_id or 'unknown'} "
            f"status=interrupted (no rounds — cache resume found nothing to do)",
            flush=True,
        )
    else:
        best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0.0) or 0.0)
        print(
            f"\n[smoke] dataset={args.dataset} "
            f"rounds={len(campaign_rounds)} "
            f"best_acc={best.get('accuracy', 0.0):.3f} "
            f"cycle={cycle_id or 'unknown'} "
            f"status=ok",
            flush=True,
        )

    if cycle_dir is not None:
        print(f"[smoke] cycle dir: {cycle_dir}", flush=True)

    session_id = getattr(result, "session_id", "") or ""
    if session_id:
        session_dir = (
            _REPO_ROOT / ".promptpotter" / "projects" / args.dataset / "sessions" / session_id
        )
        print(f"[smoke] session:     {session_id}", flush=True)
        print(f"[smoke] session dir: {session_dir}", flush=True)

    await session.backend_client.aclose()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke-test a dataset via one L1 optimization round.",
    )
    p.add_argument("--dataset", required=True, help="Key in DATASET_LOADERS")
    p.add_argument("--samples", type=int, default=5, help="Queries per candidate (sp_budget_ttest)")
    p.add_argument("--variants", type=int, default=3, help="L1 candidates per round")
    p.add_argument("--rounds", type=int, default=1, help="max_rounds")
    p.add_argument("--patience", type=int, default=1, help="l1_patience (consecutive stalls)")
    p.add_argument("--backend-url", default="http://127.0.0.1:8000")
    p.add_argument(
        "--take-over",
        action="store_true",
        help="Clear the active-session pointer if it names a different dataset",
    )
    p.add_argument(
        "--task-context",
        default="Solve the following problem. Provide only the final answer, nothing else.",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
