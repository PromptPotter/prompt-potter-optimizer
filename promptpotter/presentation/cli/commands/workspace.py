from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.cycle_addressing import cycle_target

if TYPE_CHECKING:
    import argparse

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.stores import Stores


def open_stores(args: argparse.Namespace) -> Stores:
    return local_stores(tenant=args.tenant)


def resolve_target(args: argparse.Namespace, store: Stores) -> CycleHop:
    return cycle_target(store, args.campaign, args.cycle)
