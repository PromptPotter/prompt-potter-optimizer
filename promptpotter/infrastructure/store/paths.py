"""Cycle-id parsing + per-cycle directory builders.

Module-level so callers that don't hold a ``CampaignStore`` (the emitter's
classmethod constructor, the api-layer reader) can resolve the same paths
without instantiating a store. Pure functions — no I/O, no parent walk.
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
_SWEEP_BATCH_ID_RE = re.compile(r"_sweep_([^_]+)_")


def root_cycle_id(cycle_id: str) -> str:
    """Family-root cycle id — the prefix before the FIRST sibling separator.

    Three separators are recognized: ``_fork_`` (divergence forks, minted by
    ``_fork_at_divergence``), ``_diag_`` (diagnostic-BFS siblings, minted by
    ``fork_for_diag_sibling``), and ``_sweep_`` (sweep-batch forks, minted
    by ``_run_sweep_batch``). All deterministic — no I/O, no parent walk.

    Uses FIRST separator: a sweep-of-fork like ``cycle_X_fork_Y_sweep_b1_abc``
    still roots at ``cycle_X``, since the whole family shares one telemetry
    stream regardless of intermediate fork depth."""
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag", "sweep"]:
    """Kind of THIS cycle (uses the LAST separator).

    For ``cycle_X_fork_Y_sweep_b1_abc`` the last separator is ``_sweep_``,
    so the cycle is a sweep fork (its parent ``cycle_X_fork_Y`` was a fork
    of the root). The dir layout follows the leaf kind: it lands under
    ``sweeps/b1/forks/`` regardless of the intermediate fork."""
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def _sweep_batch_id(cycle_id: str) -> str:
    """Extract ``batch_id`` from a sweep-fork cycle id (segment after ``_sweep_``)."""
    m = _SWEEP_BATCH_ID_RE.search(cycle_id)
    if m is None:
        raise ValueError(f"sweep cycle id missing batch_id segment: {cycle_id!r}")
    return m.group(1)


def root_dir_for(tenant_root: Path, cycle_id: str) -> Path:
    """Family-root campaign dir — where telemetry binds (one continuous stream
    across all forks of the family)."""
    return tenant_root / "campaigns" / validate_path_component(root_cycle_id(cycle_id))


def campaign_dir_for(tenant_root: Path, cycle_id: str) -> Path:
    """Per-cycle dir (audit). Routes by sibling kind:

    - root → ``campaigns/{cycle_id}``
    - fork (operator divergence) → ``campaigns/{root}/forks/{cycle_id}``
    - diag (diagnostic-BFS) → ``campaigns/{root}/diag/{cycle_id}``
    - sweep (sweep-batch fork) → ``campaigns/{root}/sweeps/{batch_id}/forks/{cycle_id}``
    """
    validate_path_component(cycle_id)
    kind = sibling_kind(cycle_id)
    if kind == "root":
        return tenant_root / "campaigns" / cycle_id
    root = root_cycle_id(cycle_id)
    if kind == "fork":
        return tenant_root / "campaigns" / root / "forks" / cycle_id
    if kind == "diag":
        return tenant_root / "campaigns" / root / "diag" / cycle_id
    # sweep
    batch_id = _sweep_batch_id(cycle_id)
    return tenant_root / "campaigns" / root / "sweeps" / batch_id / "forks" / cycle_id


def sweep_batch_dir_for(tenant_root: Path, root_cid: str, batch_id: str) -> Path:
    """Sweep batch dir — holds the batch's ``index.json`` + ``forks/`` subtree."""
    validate_path_component(root_cid)
    validate_path_component(batch_id)
    return tenant_root / "campaigns" / root_cid / "sweeps" / batch_id


def session_dir_for(tenant_root: Path, session_id: str) -> Path:
    """Return ``{tenant_root}/sessions/{session_id}``.

    Module-level so callers that don't hold a ``SessionStore`` (e.g. the
    emitter's classmethod constructor, which only has a raw project root)
    can resolve the same path without instantiating a store.
    """
    validate_path_component(session_id)
    return tenant_root / "sessions" / session_id
