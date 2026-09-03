"""The `/commands/{kind}` vocabulary — the one control-plane verb set every surface shares.

It sits here, and not beside the dispatcher that applies it, because the parties that must
agree on it cannot all afford to import that dispatcher: the CLI resolves its command bodies
lazily so `--help` does not pay for the application tree, and `scripts/build_ts_types.py`
emits the TypeScript union from these names alone."""

from __future__ import annotations

from typing import Literal, get_args

__all__ = [
    "ALL_DISPATCHED_KINDS",
    "CampaignConfigKind",
    "CheckinScopedKind",
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
    "change-spend-budget",
    "start-run",
    "step-cycle",
]
WorkspaceScopedKind = Literal[
    "register-backend",
    "mint-campaign",
    "replace-dataset",
    "compact-archive",
    # Workspace-scoped because a queued MINT has no cycle to address — the campaign it will
    # create does not exist yet, which is also why `pause-cycle` cannot serve one.
    "cancel-queued-run",
]
CheckinScopedKind = Literal["edit-draft-campaign", "resolve-origin", "start-checkin"]
# Campaign-scoped IN-PLACE manifest edits (the campaign persists — distinct from
# `delete`, the one lifecycle verb that removes a tree). Rewrites `campaign.json`.
CampaignConfigKind = Literal["set-allowed-models", "set-campaign-label"]

# Derived from the Literal types themselves, so every registry keyed on it — the dispatcher's
# `CAP_FOR_KIND` and `PAYLOAD_MODEL_FOR_KIND`, the CLI's `CLI_VERB_FOR_KIND`, the router's
# wired set — cannot drift from the wire. A verb reachable over HTTP but absent from a Literal
# is invisible to all four, which is how `replace-dataset` ran unguarded.
ALL_DISPATCHED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind)
    + get_args(CycleScopedKind)
    + get_args(WorkspaceScopedKind)
    + get_args(CheckinScopedKind)
    + get_args(CampaignConfigKind)
)
