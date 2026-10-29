"""Two ``new`` calls on one measured declaration share a root cycle id under different campaign
ids: ``measurements/`` pools their origin scores."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from promptpotter.application.knobs import measured_address
from promptpotter.infrastructure.store.layout import dataset_id_prefix
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.arms import PointContext
    from promptpotter.domain.campaign import RunDeclaration
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample


def mint_campaign_id(dataset_name: str) -> str:
    prefix = dataset_id_prefix(dataset_name) if dataset_name else "campaign"
    return f"{prefix}__{secrets.token_hex(3)}"


def origin_content_hash(origin: OptSearchPoint, points: PointContext, dataset: list[Sample]) -> str:
    """Config-AWARE, as the measurement key is: a connector-config edit yields a DISTINCT origin."""
    return points.job(origin).content_hash(dataset)[:12]


def root_cycle_id_of(origin_hash: str, declaration: RunDeclaration) -> str:
    """Each arm of a head-to-head is its own run; declarations differing only in spend or display are one."""
    return f"cycle_{stable_hash([origin_hash, measured_address(declaration)], length=12)}"
