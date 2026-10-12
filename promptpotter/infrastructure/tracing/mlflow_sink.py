from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.config.settings import settings
from promptpotter.infrastructure.tracing.events import CampaignStart, TraceSink

if TYPE_CHECKING:
    from promptpotter.domain.run_records import RoundClosedRecord


class MLflowSink(TraceSink):
    def __init__(self, store_base_dir: str | Path) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._traces_dir = self._tenant_root / "traces"
        self._cycle_id: str | None = None
        self._initialized = False

    def on_campaign_start(self, event: CampaignStart) -> None:
        self._cycle_id = event.cycle_id

    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None:

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return

        import mlflow

        if not self._initialized:
            # `FileStore` RAISES without this; `sqlite:///` needs deps `mlflow-skinny` omits.
            os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
            tracking_uri = (self._traces_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment_name=f"{self._tenant_id}/{self._cycle_id}")
            self._initialized = True

        params = {"round": str(record.round), "n_candidates": str(record.candidates_scored)}

        # A round that scored nothing has no accuracy, and `log_metrics` raises on a `None`.
        metrics = {"total": float(record.total)}
        if record.accuracy is not None:
            metrics["accuracy"] = record.accuracy
        tags = {
            "improved": str(record.improved).lower(),
            "winner_lineage_id": record.opt_sp.id if record.opt_sp is not None else "",
        }

        with mlflow.start_run(run_name=f"round_{record.round}"):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(tags)


__all__ = ["MLflowSink"]
