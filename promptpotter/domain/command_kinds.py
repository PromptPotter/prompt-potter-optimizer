"""The `/commands/{kind}` vocabulary, in `domain/` so its readers need not import the dispatcher."""

from __future__ import annotations

from typing import Literal, get_args

__all__ = [
    "ALL_DISPATCHED_KINDS",
    "CampaignConfigKind",
    "CheckinScopedKind",
    "CommandKind",
    "CycleScopedKind",
    "LifecycleKind",
    "WorkspaceScopedKind",
]

LifecycleKind = Literal["archive-campaign", "delete-campaign", "unarchive-campaign"]

CycleScopedKind = Literal[
    "fork-cycle",
    "skip-searchpoint",
    "delete-cycle",
    "cleanup-empty-cycles",
    "pause-cycle",
    "set-sample-lookahead",
    "origin-gate-decision",
    "change-run-limits",
    "start-run",
    "step-cycle",
    "verify-candidate",
    "grade-bench",
]
WorkspaceScopedKind = Literal[
    "register-backend",
    "mint-campaign",
    "replace-dataset",
    "compact-archive",
    # A queued MINT has no cycle to address yet, which is why `pause-cycle` cannot serve one.
    "cancel-queued-run",
    # Account-scoped: a limit on how many cycles this account holds, which no one cycle owns.
    "set-concurrent-cycles",
]
CheckinScopedKind = Literal["edit-draft-campaign", "resolve-origin", "start-checkin"]
CampaignConfigKind = Literal["set-campaign-label"]

CommandKind = (
    LifecycleKind | CycleScopedKind | WorkspaceScopedKind | CheckinScopedKind | CampaignConfigKind
)

# Derived from the Literals, so no registry keyed on it can drift from the wire.
ALL_DISPATCHED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind)
    + get_args(CycleScopedKind)
    + get_args(WorkspaceScopedKind)
    + get_args(CheckinScopedKind)
    + get_args(CampaignConfigKind)
)
