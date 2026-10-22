"""Everything this package writes to the operator's MLflow server, and it reads nothing back for the loop."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.config.settings import settings
from promptpotter.infrastructure.tracing.events import CampaignStart, TraceSink

if TYPE_CHECKING:
    from mlflow import MlflowClient
    from mlflow.entities.model_registry import PromptVersion

    from promptpotter.domain.export import PromptExport
    from promptpotter.domain.run_records import RoundClosedRecord

__all__ = ["MLflowSink", "register_export"]

_CYCLE = "promptpotter.cycle_id"
_ROUND = "promptpotter.round"
# MLflow's own rule for a prompt name: alphanumerics, hyphens, underscores and dots.
_UNNAMEABLE = re.compile(r"[^A-Za-z0-9._-]")


def _client() -> MlflowClient:
    """One server holds the runs and the registry, so a prompt version can name the run that won it."""
    # MLflow's own defaults retry for minutes, and every call here is made from inside a run.
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    # A run's stdout is its readout, and the client's run link opens on a character cp1252 cannot encode.
    os.environ["MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"] = "true"

    # The setting carries MLflow's own name on purpose: the registry's run link resolves its
    # tracking store from the process, never from the client it was called on.
    uri = os.environ["MLFLOW_TRACKING_URI"] = settings.MLFLOW_TRACKING_URI

    from mlflow import MlflowClient

    return MlflowClient(tracking_uri=uri, registry_uri=uri)


def _experiment(tenant_id: str, cycle_id: str) -> str:
    return f"{tenant_id}/{cycle_id}"


class MLflowSink(TraceSink):
    """A cycle is an experiment and each closed round a run in it."""

    def __init__(self, store_base_dir: str | Path) -> None:
        self._tenant_id = Path(store_base_dir).name
        self._experiment_id: str | None = None

    def on_campaign_start(self, event: CampaignStart) -> None:
        client = _client()
        name = _experiment(self._tenant_id, event.cycle_id)
        found = client.get_experiment_by_name(name)
        self._experiment_id = found.experiment_id if found else client.create_experiment(name)

    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None:
        if self._experiment_id is None:
            return
        client = _client()
        head, fold = record.head, record.fold
        run_id = client.create_run(
            self._experiment_id,
            run_name=f"round_{record.round}",
            tags={
                _ROUND: str(record.round),
                "improved": str(record.improved).lower(),
                "winner_lineage_id": "" if head is None else head.individual.id,
            },
        ).info.run_id
        client.log_param(run_id, "n_candidates", str(record.candidates_scored))
        client.log_metric(run_id, "total", float(0 if fold is None else fold.total))
        # A round that scored nothing has no accuracy, and a metric cannot hold a `None`.
        if fold is not None and fold.accuracy is not None:
            client.log_metric(run_id, "accuracy", fold.accuracy)
        client.set_terminated(run_id)


def register_export(export: PromptExport, *, tenant_id: str) -> None:
    """One version per (cycle, prompt text), whose tags a restated export moves; no alias is ever set."""
    if not settings.MLFLOW_TRACKING_URI:
        return
    client = _client()
    for node in export.prompts:
        name = _UNNAMEABLE.sub("-", f"{tenant_id}.{export.dataset_name}.{node}")
        _register_node_prompt(client, name, export.render(node), tenant_id=tenant_id, export=export)


def _register_node_prompt(
    client: MlflowClient, name: str, template: str, *, tenant_id: str, export: PromptExport
) -> None:
    tags = {
        _CYCLE: export.cycle_id,
        "promptpotter.campaign_id": export.campaign_id,
        "promptpotter.tool_version": export.tool_version,
        "promptpotter.dataset_hash": export.dataset_hash,
        "promptpotter.stop_reason": export.stop_reason.value,
        _ROUND: str(export.measurement.round),
        # The model the winner was won on rides here, and binds: a swap is a new run.
        "promptpotter.tuned_params": json.dumps(export.tuned_params, sort_keys=True),
        "promptpotter.bench": export.bench.line,
    }
    standing = next(
        (
            v
            for v in _versions(client, name)
            if v.tags.get(_CYCLE) == export.cycle_id and v.template == template
        ),
        None,
    )
    if standing is not None:
        for key, value in tags.items():
            if standing.tags.get(key) != value:
                client.set_prompt_version_tag(name, standing.version, key, value)
        return
    version = client.register_prompt(
        name=name,
        template=template,
        commit_message=f"PromptPotter {export.tool_version}: cycle {export.cycle_id}, "
        f"round {export.measurement.round}",
        tags=tags,
    )
    _link_winning_round(client, version, tenant_id=tenant_id, export=export)


def _link_winning_round(
    client: MlflowClient, version: PromptVersion, *, tenant_id: str, export: PromptExport
) -> None:
    """Found on the server, never handed over in memory: a bench graded later runs in another process."""
    experiment = client.get_experiment_by_name(_experiment(tenant_id, export.cycle_id))
    if experiment is None:
        return
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string=f"tags.`{_ROUND}` = '{export.measurement.round}'",
        max_results=1,
    )
    if runs:
        client.link_prompt_version_to_run(runs[0].info.run_id, version)


def _versions(client: MlflowClient, name: str) -> list[PromptVersion]:
    if client.get_prompt(name) is None:
        return []
    found: list[PromptVersion] = []
    token: str | None = None
    while True:
        page = client.search_prompt_versions(name, page_token=token)
        found.extend(page)
        token = page.token
        if not token:
            return found
