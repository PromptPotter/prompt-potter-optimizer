"""Per-cycle MLflow sink — opt-in, disabled by default. Kept on purpose even when off: operators have asked for MLflow
as a first-class observability target."""

from __future__ import annotations

import os
from pathlib import Path

from promptpotter.config.settings import settings
from promptpotter.infrastructure.tracing.events import CampaignStart, RoundEnd


class MLflowSink:
    """Logs each round as an MLflow run; experiment = ``{tenant_id}/{cycle_id}``."""

    def __init__(self, store_base_dir: str | Path) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._traces_dir = self._tenant_root / "traces"
        self._cycle_id: str | None = None
        self._initialized = False

    def on_campaign_start(self, event: CampaignStart) -> None:
        self._cycle_id = event.cycle_id

    def on_round_end(self, event: RoundEnd) -> None:

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return

        import mlflow

        if not self._initialized:
            # MLflow 3.15 put the filesystem tracking backend in maintenance mode: `FileStore`
            # RAISES unless this opts out, so flipping MLFLOW_ENABLED without it kills the round
            # the first sink fires on. The local tree IS this sink's contract — a trace mirror
            # beside `events.jsonl`, per §0 Persistence — and the migration MLflow points at
            # (`sqlite:///`) wants SQLAlchemy + alembic, which `mlflow-skinny` deliberately omits.
            os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
            tracking_uri = (self._traces_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment_name=f"{self._tenant_id}/{self._cycle_id}")
            self._initialized = True

        params: dict[str, str] = {
            "round": str(event.round_num),
        }
        if event.model:
            params["model"] = event.model
        if event.n_candidates:
            params["n_candidates"] = str(event.n_candidates)

        # A round that scored nothing has no accuracy, and `log_metrics` raises on a `None`.
        metrics = {"total": float(event.total)}
        if event.accuracy is not None:
            metrics["accuracy"] = event.accuracy
        tags = {
            "improved": str(event.improved).lower(),
            "next_action": event.next_action,
            "winner_lineage_id": event.winner_lineage_id,
        }

        with mlflow.start_run(run_name=f"round_{event.round_num}"):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(tags)


__all__ = ["MLflowSink"]
