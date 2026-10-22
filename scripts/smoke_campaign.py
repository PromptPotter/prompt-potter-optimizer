from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Force utf-8 stdout so Windows cp1252 console doesn't choke on box-drawing characters.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from promptpotter.application.account import local_stores  # noqa: E402
from promptpotter.application.campaign_config import CampaignConfig  # noqa: E402
from promptpotter.application.datasets.authored import dataset_campaign_block  # noqa: E402
from promptpotter.application.datasets.loaders import (  # noqa: E402
    dataset_loader,
    loadable_dataset_names,
)
from promptpotter.application.embedded_run import open_session, run_campaign  # noqa: E402
from promptpotter.application.pipeline_resolve import (  # noqa: E402
    configure_and_apply_pipeline,
)
from promptpotter.domain.run_mode import RunMode  # noqa: E402
from promptpotter.domain.spend import Budget  # noqa: E402
from promptpotter.infrastructure.store.dataset_access import resolve_dataset  # noqa: E402
from promptpotter.presentation.terminal.completion import report_completion  # noqa: E402
from promptpotter.shared.errors import DatasetAccessError  # noqa: E402


def _build_config(
    dataset_dir: Path,
    *,
    samples: int,
    variants: int,
    rounds: int,
    patience: int,
) -> dict[str, Any]:
    return {
        "scoring": _infer_scoring(dataset_dir),
        "optimization": {
            "budget": {"rounds": rounds},
            "degradation_threshold": 0.4,
            "nodes": {
                "adaptive_queue": {"config": {"sp_budget_round": samples}},
                "l1_generate": {"config": {"n_variants": variants}},
                "escalation": {
                    "config": {"l1_patience": patience, "l2_patience": 1, "l3_patience": 1}
                },
            },
        },
    }


def _infer_scoring(dataset_dir: Path) -> str:
    formula = dataset_campaign_block(dataset_dir).get("scoring")
    return str(formula) if formula else "label_match(predicted, ground_truth)"


async def _run(args: argparse.Namespace) -> int:
    stores = local_stores(tenant=None)
    try:
        dataset_dir = resolve_dataset(stores, args.dataset).directory
    except DatasetAccessError:
        dataset_dir = None
    if dataset_dir is None or dataset_loader(stores, dataset_dir) is None:
        known = ", ".join(loadable_dataset_names(stores)) or "(none)"
        print(
            f"[smoke] ERROR: no source resolves dataset {args.dataset!r}. Known: {known}",
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

    session = await open_session(args.dataset)

    schema = session.pipeline_schema
    if not schema.available_models:
        print(
            f"[smoke] ERROR: pipeline schema for {args.dataset!r} declares no "
            f"available_models. Add an `available_models: [...]` list to "
            f"the `pipeline` section of {args.dataset}'s dataset.yaml so the L1 validation rail "
            f"can reject candidates that propose unsupported models.",
            file=sys.stderr,
        )
        return 4

    campaign_config = CampaignConfig.model_validate(
        _build_config(
            dataset_dir,
            samples=args.samples,
            variants=args.variants,
            rounds=args.rounds,
            patience=args.patience,
        )
    )
    configure_and_apply_pipeline(session, campaign_config)

    if not session.bank.samples:
        print("[smoke] ERROR: dataset loader returned no items", file=sys.stderr)
        return 3

    result = await run_campaign(
        session,
        campaign_config,
        readout_sink=print,
        budget=Budget(),
        mode=RunMode(),
    )
    report_completion(result, session=session)
    origin = None if result.origin is None else result.origin.accuracy
    print(
        f"[smoke] origin: {origin.value:.3f}"
        if origin is not None
        else "[smoke] origin: not measured (no scoreable row)",
        flush=True,
    )

    cycle_id = result.cycle_id or ""
    cycle_dir = (
        project_dir / "campaigns" / session.campaign_id / "cycles" / cycle_id if cycle_id else None
    )

    if not result.rounds:
        print(
            f"\n[smoke] dataset={args.dataset} "
            f"rounds=0 "
            f"cycle={cycle_id or 'unknown'} "
            f"status=interrupted (no rounds — cache resume found nothing to do)",
            flush=True,
        )
    else:
        print(
            f"\n[smoke] dataset={args.dataset} "
            f"rounds={result.n_rounds_after_origin} "
            f"best_acc={result.result_accuracy:.3f} (round {result.result_round}) "
            f"cycle={cycle_id or 'unknown'} "
            f"stop={result.stop_reason} "
            f"status=ok",
            flush=True,
        )

    if cycle_dir is not None:
        print(f"[smoke] cycle dir: {cycle_dir}", flush=True)

    await session.backend_client.aclose()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke-test a dataset via one L1 optimization round.",
    )
    p.add_argument(
        "--dataset",
        required=True,
        help="Dataset name whose definition declares a source (see loadable_dataset_names())",
    )
    p.add_argument("--samples", type=int, default=5, help="Queries per candidate (sp_budget_round)")
    p.add_argument("--variants", type=int, default=3, help="L1 candidates per round")
    p.add_argument("--rounds", type=int, default=1, help="budget.rounds")
    p.add_argument("--patience", type=int, default=1, help="l1_patience (consecutive stalls)")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
