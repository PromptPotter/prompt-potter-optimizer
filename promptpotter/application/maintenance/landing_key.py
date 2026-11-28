"""The key a data root lands under (`landing.py::LandedStamp`): derived from the shapes it stores,
so a code move that keeps every shape keeps the key. A process derives it once, and a data root
keeps it filed by the package's bytes (`config/paths.py::LANDING_KEYS_DIR`), so an entry on
unchanged code hashes no schema."""

from __future__ import annotations

import hashlib
import sys
from functools import cache
from pathlib import Path

import pydantic

from promptpotter.config.paths import LANDING_KEYS_DIR, user_data_root
from promptpotter.infrastructure.store.data_root import KINDS, row_schema
from promptpotter.infrastructure.store.io import read_text_optional, write_text
from promptpotter.shared.hashing import stable_hash

_PACKAGE = Path(__file__).resolve().parents[2]


def derived_landing_key() -> str:
    """Every row's patterns, fate and shape, a ledger's every record kind's: a move in any is what a
    landing has to land, and a migration ships under the reshape it lands (decisions.md § 0011)."""
    return stable_hash({kind.name: row_schema(kind) for kind in KINDS}, length=16)


@cache
def _code_digest() -> str:
    """What the derived key is a function of: the package's bytes and its schema builder's version."""
    held = hashlib.sha256(f"{sys.version}|{pydantic.VERSION}".encode())
    for path in sorted(_PACKAGE.rglob("*.py")):
        held.update(path.relative_to(_PACKAGE).as_posix().encode())
        held.update(path.read_bytes())
    return held.hexdigest()[:32]


def _filed(digest: str) -> Path:
    return user_data_root() / LANDING_KEYS_DIR / digest


@cache
def _derived(digest: str) -> str:
    return read_text_optional(_filed(digest)).strip() or derived_landing_key()


def landing_key() -> str:
    """This code's key, filed in the data root that asked once derived."""
    key = _derived(digest := _code_digest())
    if not _filed(digest).is_file():
        write_text(_filed(digest), key)
    return key
