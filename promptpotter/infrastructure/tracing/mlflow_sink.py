"""Per-cycle MLflow sink — opt-in, disabled by default. Kept on purpose even when off: operators have asked for MLflow
as a first-class observability target."""

from __future__ import annotations

from pathlib import Path

from promptpotter.config.settings import settings
from promptpotter.infrastructure.tracing.events import CampaignStart, RoundEnd


class MLflowSink:
    """Logs each round as an MLflow run; experiment = ``{tenant_id}/{cycle_id}``."""

    def __init__(self, store_base_dir: str | Path) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._archive_dir = self._tenant_root / "archive"
        self._cycle_id: str | None = None
        self._initialized = False

    def on_campaign_start(self, event: CampaignStart) -> None:
        if event.session_id:
            self._cycle_id = event.session_id

    def on_round_end(self, event: RoundEnd) -> None:

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return

        import mlflow

        if not self._initialized:
            tracking_uri = (self._archive_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment_name=f"{self._tenant_id}/{self._cycle_id}")
            self._initialized = True

        params: dict[str, str] = {
            "round": str(event.round_num),
        }
        if event.model:
            params["model"] = event.model
        if event.n_variants:
            params["n_variants"] = str(event.n_variants)

        metrics = {
            "accuracy": event.accuracy,
            "total": float(event.total),
        }
        tags = {
            "improved": str(event.improved).lower(),
            "next_action": event.next_action,
            "winner_prompt_fields_id": event.winner_prompt_fields_id,
        }

        with mlflow.start_run(run_name=f"round_{event.round_num}"):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(tags)


__all__ = ["MLflowSink"]
