"""One canonical reader for an authored dataset's on-disk config.

An authored dataset dir (``datasets/{name}/`` or a tenant upload at
``projects/{tenant}/datasets/{slug}/``) carries ``campaign.yaml`` (config
wrapped under the outer ``campaign_config`` key), ``pipeline.yaml``
(``backend_type`` + per-node overlay), and ``task_description.md``. The CLI
``new`` config-read, the launcher web-launch read, ``draft_from_dataset``, and
``SessionCtx`` all share this single reader.

Rows (``cache.json``) are deliberately NOT read here: the three row consumers
source them differently (the draft reads a dir's ``cache.json`` directly;
``new``/launcher go through ``TenantDatasetStore`` with a benchmark/registry
download fallback), and folding that 3-tier sourcing policy in would force the
reader to take ``Stores``. The reader stays file-only — dir in, dataclass out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from promptpotter.application.config import CampaignConfig
from promptpotter.application.config import load_campaign_config as validate_campaign_config
from promptpotter.application.datasets.csv_ingest import read_candidate_library_file
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.shared.errors import StoredConfigInvalidError


@dataclass(frozen=True, slots=True)
class AuthoredDataset:
    """An authored dataset's validated config, parsed once from its files."""

    campaign_config: CampaignConfig
    """Validated ``campaign.yaml`` config — the outer ``campaign_config`` key already unwrapped."""

    task_description: str
    """``task_description.md`` text, stripped; ``""`` when the file is absent."""

    backend_type: str
    """``pipeline.yaml::backend_type``, lowercased; ``""`` when the field is absent.

    Intentionally non-raising so each consumer decides: the draft path defaults
    to ``DEFAULT_CONNECTOR``, the launcher raises ``LaunchError`` on a blank."""

    pipeline_nodes: dict[str, Any]
    """The WHOLE ``pipeline.yaml::nodes.{name}`` dicts (config + optimizer + …),
    not just ``nodes.*.config``. ``_merged_backend_nodes`` shallow-merges each
    sub-block, so dropping to ``nodes.*.config`` (as ``load_dataset_node_overlay``
    does) would silently lose ``optimizer.param_allowed_values`` locks."""

    active_steps: list[str]
    """``pipeline.yaml::pipelines.default`` — the dataset's chosen pipeline (e.g.
    the full ``cache_lookup → … → token_matching`` vs a bare ``llm_only``). ``[]``
    when absent, in which case the connector's ``default_pipeline`` applies. The
    draft path carries this so reusing a dataset PRESERVES its pipeline instead of
    resetting to the connector default."""

    candidate_library: tuple[str, ...]
    """``candidate_library.txt`` — the per-pipeline origin's target list, part of
    the origin spec (a ``candidate_source`` node ranks each query against it).
    ``()`` when absent. The draft path carries this so reopening a dataset surfaces
    its already-dropped library as FULFILLED (the origin HOLDS the value); the run
    reads the same file directly for the term-index union."""


def dataset_campaign_path(dataset_dir: Path) -> Path:
    """The dataset's campaign template. **The one place this filename is spelled.**

    Deliberately NOT the same file as the minted manifest ``campaigns/{id}/campaign.json``
    — that one is machine-written and stays JSON. The two carry incompatible schemas, so
    the extension is now also the tell: a ``.yaml`` here is the template, a ``.json``
    there is the manifest.
    """
    return dataset_dir / "campaign.yaml"


def read_campaign_config_file(path: Path) -> dict[str, Any]:
    """Read a dataset's ``campaign.yaml`` → the config under its ``campaign_config`` key.
    ``{}`` when the file is absent, empty, or whitespace-only.

    **The sole reader of this file.** It is the dataset **template** at
    ``datasets/{name}/campaign.yaml`` — always wrapped in ``campaign_config``, which is what
    every writer emits (``draft_build._build_default_campaign_json``, ingest's
    ``write_committed_dataset``) and what ``TenantDatasetStore._rewrite_campaign_self_ref``
    requires. It is NOT the minted **manifest** at ``campaigns/{id}/campaign.json`` (a frozen
    :class:`Campaign`, ``extra="forbid"``, owned by ``CampaignStore``). Two incompatible schemas
    under one stem: check which tree the path is under before assuming a shape — and read the
    template through here, never with a hand-rolled ``.get("campaign_config", data)``, which
    quietly accepts an unwrapped shape no writer produces.

    Unreadable bytes raise :class:`StoredConfigInvalidError` naming *path* — this is the
    only layer that still knows which file it was, so a bare parse error escaping
    here reaches the operator as a nameless 500."""
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise StoredConfigInvalidError(path=str(path), reason=f"not valid YAML — {exc}") from exc
    result: dict[str, Any] = (parsed or {}).get("campaign_config") or {}
    return result


def load_dataset_campaign_config(path: Path) -> CampaignConfig:
    """A dataset's ``campaign.yaml`` → its validated :class:`CampaignConfig`.

    The read-and-validate pair, owned once. Four call sites re-composed
    ``load_campaign_config(read_campaign_config_file(path))`` by hand — three dataset
    reads (``/datasets/{name}/pipeline``, ``/preview``, ``/origins``) plus
    :func:`read_authored_dataset` behind the draft mint — and each dropped the path on
    the floor, so the same stale key 500'd four ways with nothing naming the file.

    ``CampaignConfig`` is ``extra="forbid"``: a knob dropped from the model makes every
    file still naming it unloadable. That is a property of *our* deploy, not of the
    caller's request, so it surfaces as :class:`StoredConfigInvalidError` carrying the
    path and the offending key — and the remedy (``promptpotter restamp --apply``) runs
    on every deploy. Callers wanting the raw dict to merge still use
    :func:`read_campaign_config_file`.
    """
    try:
        return validate_campaign_config(read_campaign_config_file(path))
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise StoredConfigInvalidError(path=str(path), reason=reason) from exc


def read_authored_dataset(dataset_dir: Path) -> AuthoredDataset:
    """Validates ``campaign.yaml`` through :class:`CampaignConfig` (``extra="forbid"``)
    — a malformed/extra-key config raises :class:`StoredConfigInvalidError` naming the
    file and the key, rather than silently defaulting. Does not read ``cache.json`` /
    rows (see module docstring).
    """
    campaign_config = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))

    task_path = dataset_dir / "task_description.md"
    task_description = task_path.read_text(encoding="utf-8").strip() if task_path.is_file() else ""

    pipeline_path = dataset_pipeline_path(dataset_dir)
    pipeline: dict[str, Any] = read_yaml_optional(pipeline_path) or {}
    backend_type = str(pipeline.get("backend_type") or "").lower()
    pipeline_nodes = dict(pipeline.get("nodes") or {})
    active_steps = [str(s) for s in (pipeline.get("pipelines") or {}).get("default") or []]

    return AuthoredDataset(
        campaign_config=campaign_config,
        task_description=task_description,
        backend_type=backend_type,
        pipeline_nodes=pipeline_nodes,
        active_steps=active_steps,
        candidate_library=read_candidate_library_file(dataset_dir),
    )


__all__ = [
    "AuthoredDataset",
    "load_dataset_campaign_config",
    "read_authored_dataset",
    "read_campaign_config_file",
]
