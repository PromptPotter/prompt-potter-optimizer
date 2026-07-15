"""``CommandDispatcher`` package — sole writer of ``CommandRecord`` at the API seam.

The dispatch / record / apply / ack pipeline lives in :mod:`.dispatcher`; the
free payload-coercion helpers + internal signal types live in :mod:`.helpers`.
The public surface (``CommandDispatcher``, ``CommandAcceptedBody``, and the
inbound-kind ``Literal`` aliases) is re-exported here so callers keep importing
from ``presentation.api.middleware.command_dispatcher`` unchanged.
"""

from __future__ import annotations

from promptpotter.presentation.api.middleware.command_dispatcher.dispatcher import (
    CampaignConfigKind,
    CommandAcceptedBody,
    CommandDispatcher,
    CycleScopedKind,
    LifecycleKind,
    WorkspaceBackendKind,
)

__all__ = [
    "CampaignConfigKind",
    "CommandAcceptedBody",
    "CommandDispatcher",
    "CycleScopedKind",
    "LifecycleKind",
    "WorkspaceBackendKind",
]
