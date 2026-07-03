"""``cmd_verify`` — re-score one campaign candidate on more samples.

Operator names campaign + candidate (``C{round}.{idx}`` or ``C0``); this shell
resolves the needle-style CLI args to concrete ids/labels, calls the
:mod:`application.verify` use-case (candidate resolution + scoring +
``DiagnosticRunRecord`` sidecar), and formats the result.

Not a cycle/fork/sweep: no ledger event, no round_id; persistence is into the
workspace ``archive/`` tree only."""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from promptpotter.application.verify import VerifyError, verify_candidate
from promptpotter.infrastructure.store import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


def _resolve_campaign(stores: Stores, needle: str) -> str:
    """Resolve *needle* to a campaign id; accepts full id, 6-hex suffix, or unambiguous prefix."""
    ids = stores.campaigns.list_campaign_ids()
    if needle in ids:
        return needle
    candidates = [cid for cid in ids if cid.endswith(f"__{needle}") or cid.startswith(needle)]
    if needle and not candidates:
        candidates = [cid for cid in ids if needle in cid]
    if not candidates:
        raise SystemExit(f"ERROR: no campaign matches {needle!r}.")
    if len(candidates) > 1:
        raise SystemExit(
            f"ERROR: {needle!r} matches {len(candidates)} campaigns: "
            f"{', '.join(candidates[:5])}{'…' if len(candidates) > 5 else ''}. "
            "Pass the full id."
        )
    return candidates[0]


def _resolve_cycle(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """Resolve a cycle id within *campaign_id*; ``hint=None`` auto-picks the sole cycle (raises on ambiguity)."""
    cycles_dir = stores.campaigns.campaign_root_dir(campaign_id) / "cycles"
    if not cycles_dir.exists():
        raise SystemExit(f"ERROR: campaign {campaign_id!r} has no cycles/ directory.")
    ids = sorted(p.name for p in cycles_dir.iterdir() if p.is_dir())
    if not ids:
        raise SystemExit(f"ERROR: campaign {campaign_id!r} has no cycles on disk.")
    if hint:
        matches = [cid for cid in ids if cid == hint or cid.startswith(hint) or hint in cid]
        if not matches:
            raise SystemExit(f"ERROR: no cycle in {campaign_id!r} matches {hint!r}.")
        if len(matches) > 1:
            raise SystemExit(
                f"ERROR: {hint!r} matches {len(matches)} cycles in {campaign_id!r}: "
                f"{', '.join(matches[:5])}."
            )
        return matches[0]
    if len(ids) > 1:
        raise SystemExit(
            f"ERROR: campaign {campaign_id!r} has {len(ids)} cycles; pass --cycle <prefix>. "
            f"Available: {', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}."
        )
    return ids[0]


def _parse_label(label: str) -> tuple[int, int]:
    """``C0`` ⇒ ``(0, 0)`` (origin); ``C{round}.{n}`` ⇒ ``(round, n-1)`` (labels 1-indexed, on-disk 0-indexed)."""
    if label == "C0":
        return 0, 0
    if not label.startswith("C") or "." not in label:
        raise SystemExit(f"ERROR: bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}.")
    round_part, idx_part = label[1:].split(".", 1)
    try:
        round_num = int(round_part)
        idx_one_based = int(idx_part)
    except ValueError as exc:
        raise SystemExit(f"ERROR: bad candidate label {label!r}: {exc}") from None
    if idx_one_based < 1:
        raise SystemExit(f"ERROR: candidate index in {label!r} must be ≥ 1.")
    return round_num, idx_one_based - 1


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    """Re-score a campaign candidate on N additional samples; persist the workspace verdict."""
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    stores = build_stores(identity)
    campaign_id = _resolve_campaign(stores, args.campaign)
    cycle_id = _resolve_cycle(stores, campaign_id, args.cycle)
    round_num, cand_idx = _parse_label(args.label)

    try:
        outcome = await verify_candidate(
            stores=stores,
            identity=identity,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            round_num=round_num,
            cand_idx=cand_idx,
            label=args.label,
            samples=args.samples,
            seed=args.seed,
            log=logger.info if get_verbose() else None,
        )
    except VerifyError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    if outcome.record is None:
        return CommandResult(
            human=(
                f"{args.label}: every sample in the {outcome.dataset_name} bank is "
                f"already measured for this config ({outcome.already_measured} total). "
                "Nothing to add."
            ),
        )

    record = outcome.record
    human = (
        f"{args.label}: acc {record.source_campaign_accuracy:.3f}→{record.workspace_accuracy:.3f} "
        f"(cf {record.source_campaign_composite:.3f}→{record.workspace_composite:.3f}) "
        f"on {record.workspace_n} samples (+{record.samples_added} new from "
        f"{record.source_campaign_n} in campaign"
        + (f", {outcome.cache_replays} cache-replay" if outcome.cache_replays else "")
        + ")."
    )
    return CommandResult(data=record.model_dump(), human=human)


__all__ = ["cmd_verify"]
