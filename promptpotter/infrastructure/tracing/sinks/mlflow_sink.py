"""Per-cycle MLflow sink — user-requested integration, opt-in.

Logs each round as an MLflow run under ``library/mlruns/``. Disabled by
default; flip ``settings.MLFLOW_ENABLED`` to turn on. Kept on purpose
even when off — operators have requested MLflow as a first-class
observability target.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.infrastructure.tracing.events import CampaignStart, RoundEnd


class MLflowSink:
    """Logs each round as an MLflow run; experiment = ``{tenant_id}/{cycle_id}``."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._library_dir = self._tenant_root / "library"
        self._backend_id = backend_id
        self._cycle_id: str | None = None
        self._initialized = False

    def on_campaign_start(self, event: CampaignStart) -> None:
        if event.session_id:
            self._cycle_id = event.session_id

    def on_round_end(self, event: RoundEnd) -> None:
        from promptpotter.config.settings import settings

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return
        import mlflow

        if not self._initialized:
            tracking_uri = (self._library_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(name=f"{self._tenant_id}/{self._cycle_id}")
            self._initialized = True

        params: dict[str, str] = {
            "round": str(event.round_num),
            "temperature": str(event.temperature),
        }
        if event.model:
            params["model"] = event.model
        if event.n_variants:
            params["n_variants"] = str(event.n_variants)

        metrics = {
            "accuracy": event.accuracy,
            "hits": float(event.hits),
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
