from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores

if TYPE_CHECKING:
    import argparse

    from promptpotter.infrastructure.store.stores import Stores


def open_stores(args: argparse.Namespace) -> Stores:
    return local_stores(tenant=args.tenant)
