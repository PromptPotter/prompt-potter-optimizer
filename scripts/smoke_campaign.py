"""Smoke-test a dataset end-to-end: one L1 round, minimal budget. ~90s on gpt-oss-120b via Groq."""

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

from promptpotter.application.campaign_config import load_campaign_config  # noqa: E402
from promptpotter.application.datasets.authored import (  # noqa: E402
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.datasets.loaders import (  # noqa: E402
    dataset_loader,
    loadable_dataset_names,
)
from promptpotter.application.embedded_run import (  # noqa: E402
    mint_and_score_origin,
    open_session,
    run_campaign,
)
from promptpotter.application.pipeline_resolve import (  # noqa: E402
    configure_and_apply_pipeline,
)
from promptpotter.presentation.views.completion import report_completion  # noqa: E402
from promptpotter.presentation.views.display import set_display_tags  # noqa: E402
from promptpotter.presentation.views.live.display import LiveDisplay  # noqa: E402


def _build_config(
    dataset: str,
    *,
    samples: int,
    variants: int,
    rounds: int,
    patience: int,
) -> dict[str, Any]:
    return {
        "dataset_name": dataset,
        "scoring": _infer_scoring(dataset),
        "sp_budget_ttest": samples,
        "exclude_nodes": [],
        "pipeline_overrides": {},
        "optimization": {
            "l1_patience": patience,
            "max_rounds": rounds,
            "n_variants": variants,
            "degradation_threshold": 0.4,
            "l2_patience": 1,
            "l3_patience": 1,
        },
    }


def _infer_scoring(dataset: str) -> str:
    """Prefer the dataset's own declared formula; fall back to ``exact_match``. Through the one
    reader — this parsed the YAML template with ``json.loads`` behind a bare ``except``, so it
    never once read a formula and every smoke run scored on the fallback."""
    formula = read_campaign_config_file(
        dataset_campaign_path(_REPO_ROOT / "datasets" / dataset)
    ).get("scoring")
    return str(formula) if formula else "exact_match(predicted, ground_truth)"


async def _run(args: argparse.Namespace) -> int:
    if dataset_loader(args.dataset) is None:
        known = ", ".join(loadable_dataset_names()) or "(none)"
        print(
            f"[smoke] ERROR: no loader resolves dataset {args.dataset!r}. "
            f"Known: {known} (plus any justlogic-d<depths>, e.g. justlogic-d34)",
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

    session = await open_session(
        args.dataset,
        backend_url=args.backend_url,
        backend_id=args.dataset,
        on_status=print,
    )

    schema = session.pipeline_schema
    if not schema.available_models:
        print(
            f"[smoke] ERROR: pipeline schema for {args.dataset!r} declares no "
            f"available_models. Add an `available_models: [...]` list to "
            f"datasets/{args.dataset}/pipeline.yaml so the L1 validation rail "
            f"can reject candidates that propose unsupported models.",
            file=sys.stderr,
        )
        return 4

    campaign_config = load_campaign_config(
        _build_config(
            args.dataset,
            samples=args.samples,
            variants=args.variants,
            rounds=args.rounds,
            patience=args.patience,
        )
    )
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=print)
    set_display_tags(session.pipeline_schema)

    train_slice = (session.samples or [])[: args.samples]
    if not train_slice:
        print("[smoke] ERROR: dataset loader returned no items", file=sys.stderr)
        return 3

    observers, dataset_obj, origin = await mint_and_score_origin(
        session,
        train_slice,
        campaign_config,
        pipeline_params=pipeline_params,
        display=LiveDisplay.for_campaign(session, campaign_config),
        on_status=print,
    )
    report = origin.report
    print(
        f"[smoke] origin: {report.accuracy:.3f} "
        f"({round(report.accuracy * report.total)}/{report.total})",
        flush=True,
    )

    result = await run_campaign(
        observers,
        dataset_obj,
        origin,
        campaign_config,
        session=session,
    )
    report_completion(result, session=session)

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
            f"rounds={result.n_l1_rounds} "
            f"best_acc={result.best_accuracy:.3f} (round {result.best_round}) "
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
        help="Dataset name any loader resolves (see loadable_dataset_names())",
    )
    p.add_argument("--samples", type=int, default=5, help="Queries per candidate (sp_budget_ttest)")
    p.add_argument("--variants", type=int, default=3, help="L1 candidates per round")
    p.add_argument("--rounds", type=int, default=1, help="max_rounds")
    p.add_argument("--patience", type=int, default=1, help="l1_patience (consecutive stalls)")
    p.add_argument("--backend-url", default="http://127.0.0.1:8000")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
