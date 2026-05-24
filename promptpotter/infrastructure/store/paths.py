"""Campaign / cycle directory builders + cycle-id parsing. Pure — no I/O, no parent walk.

On-disk shape: ``campaigns/{campaign_id}/`` holds ``campaign.json`` + ``log.md`` +
``cycles/{cycle_id}/`` (every cycle flat under one ``cycles/``). New campaigns
mint ``campaign_id = {dataset}__{rand6_hex}`` per ``new``; root cycle is
``cycle_{target_hash[:12]}``. Sibling kind + sweep batch id live in
``cycles/{cycle_id}/index.json``, not the path.

**Legacy shape (readers parse, writers don't emit).** Pre-existing campaigns from
the ``{dataset}__{declaration_hash}`` scheme carry a session forest with
``_s{N}`` suffixes on session-root cycle ids. The suffix only disambiguates the
session root; :func:`session_index` parses it, :func:`root_cycle_id` /
:func:`sibling_kind` ignore it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from promptpotter.infrastructure.store.base import validate_path_component

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECTS_ROOT = _REPO_ROOT / ".promptpotter" / "projects"
DEFAULT_TENANT_ID = "default"
DEFAULT_DATASETS_ROOT = _REPO_ROOT / "datasets"


_SIBLING_SEP_RE = re.compile(r"_(fork|diag|sweep)_")
_SIBLING_LAST_SEP_RE = re.compile(r"_(fork|diag|sweep)_(?!.*_(fork|diag|sweep)_)")
_SESSION_SUFFIX_RE = re.compile(r"_s(\d+)$")
_DATASET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_dataset_name(name: str) -> str:
    """Stricter than :func:`validate_path_component` — dataset names don't carry dots.

    Raises ``ValueError`` on invalid; returns *name* unchanged otherwise.
    """
    if not name or not _DATASET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. "
            "Only alphanumerics, hyphens, and underscores are allowed."
        )
    return name


def root_cycle_id(cycle_id: str) -> str:
    """Family-root cycle id — prefix before the FIRST sibling separator (``_fork_``/``_diag_``/``_sweep_``).

    ``cycle_X_fork_Y_sweep_b1_abc`` roots at ``cycle_X``.
    """
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag", "sweep"]:
    """Kind of THIS cycle (LAST separator); ``cycle_X_fork_Y_sweep_b1_abc`` ⇒ ``sweep``."""
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def session_index(cycle_id: str) -> int:
    """Session ordinal from a session-root id; ``cycle_{hash}_s3`` ⇒ 3, bare ⇒ 1. Session-root only."""
    m = _SESSION_SUFFIX_RE.search(cycle_id)
    return int(m.group(1)) if m else 1


def campaign_root_dir_for(tenant_root: Path, campaign_id: str) -> Path:
    """Campaign dir — ``campaign.json`` + ``log.md`` + ``hard_samples.json`` + ``cycles/``. Per-session telemetry binds one level down."""
    return tenant_root / "campaigns" / validate_path_component(campaign_id)


def cycle_dir_for(tenant_root: Path, campaign_id: str, cycle_id: str) -> Path:
    """Per-cycle dir ``campaigns/{campaign_id}/cycles/{cycle_id}``; flat — sibling kind in ``index.json``, not the path."""
    validate_path_component(campaign_id)
    validate_path_component(cycle_id)
    return tenant_root / "campaigns" / campaign_id / "cycles" / cycle_id


def sweep_batch_dir_for(tenant_root: Path, campaign_id: str, batch_id: str) -> Path:
    """``campaigns/{campaign_id}/sweeps/{batch_id}`` — batch ``index.json`` + ``summary.md``; fork *cycles* live flat under ``cycles/``."""
    validate_path_component(batch_id)
    return campaign_root_dir_for(tenant_root, campaign_id) / "sweeps" / batch_id


def session_dir_for(tenant_root: Path, session_id: str) -> Path:
    """Return ``{tenant_root}/sessions/{session_id}``."""
    validate_path_component(session_id)
    return tenant_root / "sessions" / session_id


__all__ = [
    "DEFAULT_DATASETS_ROOT",
    "DEFAULT_PROJECTS_ROOT",
    "DEFAULT_TENANT_ID",
    "campaign_root_dir_for",
    "cycle_dir_for",
    "root_cycle_id",
    "session_dir_for",
    "session_index",
    "sibling_kind",
    "sweep_batch_dir_for",
    "validate_dataset_name",
]
