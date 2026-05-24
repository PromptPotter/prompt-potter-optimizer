"""Campaign / cycle directory builders + cycle-id parsing.

Module-level so callers that don't hold a ``CampaignStore`` (the emitter's
classmethod constructor, the api-layer reader) can resolve the same paths
without instantiating a store. Pure functions — no I/O, no parent walk.

On-disk shape: ``campaigns/{campaign_id}/`` holds the campaign manifest
(``campaign.json``), the campaign digest (``log.md``), and
``cycles/{cycle_id}/`` — every cycle flat under one ``cycles/`` dir. New
campaigns mint a fresh ``campaign_id = {dataset}__{rand6_hex}`` per
``new`` invocation; their root cycle is the bare
``cycle_{target_hash[:12]}``. Per-session telemetry (``dashboard.json``)
binds at each session-family root cycle dir. The cycle's sibling kind +
sweep batch id live in ``cycles/{cycle_id}/index.json`` metadata, not
the path.

**Legacy on-disk shape (readers parse, writers don't emit).** Pre-existing
campaigns minted under the previous content-addressed
``{dataset}__{declaration_hash}`` scheme carry a forest of N session roots
under one ``campaign_id`` — ``cycle_{hash}`` for session 1 plus
``cycle_{hash}_s2`` / ``_s3`` / … for sessions 2 onwards (where N counts
``new`` invocations on the unchanged declaration). The ``_s{N}`` suffix
only disambiguates the session root dir; it is not a sibling separator.
:func:`session_index` parses the ordinal; :func:`root_cycle_id` and
:func:`sibling_kind` ignore it. Readers stay because pre-rewrite campaigns
still live on disk; new campaigns never emit ``_s{N}``.
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


def root_cycle_id(cycle_id: str) -> str:
    """Family-root cycle id — the prefix before the FIRST sibling separator.

    Three separators are recognized: ``_fork_`` (divergence forks), ``_diag_``
    (diagnostic-BFS siblings), and ``_sweep_`` (sweep-batch forks). All
    deterministic — no I/O, no parent walk.

    Uses FIRST separator: a sweep-of-fork like ``cycle_X_fork_Y_sweep_b1_abc``
    still roots at ``cycle_X``."""
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag", "sweep"]:
    """Kind of THIS cycle (uses the LAST separator).

    For ``cycle_X_fork_Y_sweep_b1_abc`` the last separator is ``_sweep_``,
    so the cycle is a sweep fork."""
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def session_index(cycle_id: str) -> int:
    """Session ordinal encoded in a session-root cycle id.

    ``cycle_{hash}`` → 1 (the campaign's first session); ``cycle_{hash}_s3``
    → 3. Defined for session roots only — the ``_s{N}`` suffix never appears
    on a fork/diag/sweep cycle."""
    m = _SESSION_SUFFIX_RE.search(cycle_id)
    return int(m.group(1)) if m else 1


def campaign_root_dir_for(tenant_root: Path, campaign_id: str) -> Path:
    """Campaign dir — holds ``campaign.json``, ``log.md``, the campaign-scope
    ``hard_samples.json``, and the ``cycles/`` subtree. Per-session telemetry
    binds one level down, at each session-family root cycle dir, not here."""
    return tenant_root / "campaigns" / validate_path_component(campaign_id)


def cycle_dir_for(tenant_root: Path, campaign_id: str, cycle_id: str) -> Path:
    """Per-cycle dir — ``campaigns/{campaign_id}/cycles/{cycle_id}``.

    Flat: root, fork, diag, and sweep cycles all land directly under
    ``cycles/``. The sibling kind is recorded in the cycle's ``index.json``,
    never encoded into the directory tree."""
    validate_path_component(campaign_id)
    validate_path_component(cycle_id)
    return tenant_root / "campaigns" / campaign_id / "cycles" / cycle_id


def sweep_batch_dir_for(tenant_root: Path, campaign_id: str, batch_id: str) -> Path:
    """Sweep-batch dir — ``campaigns/{campaign_id}/sweeps/{batch_id}``.

    Holds the batch ``index.json`` + ``summary.md`` (campaign-level
    artifacts). The sweep-fork *cycles* themselves live flat under
    ``cycles/``; only their ``index.json`` records the ``sweep_batch_id``."""
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
]
