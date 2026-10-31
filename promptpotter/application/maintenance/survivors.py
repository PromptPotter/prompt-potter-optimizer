"""The files that outlive a release; a landing refuses while one on the box does not load (`deploy-linux/update.sh`)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from pydantic import TypeAdapter

from promptpotter.config.paths import PROJECTS_DIR
from promptpotter.infrastructure.identity.default_claim import DefaultClaim
from promptpotter.infrastructure.identity.paths import IDENTITY_DIR, IdentityPaths
from promptpotter.infrastructure.store.user_store import USER_FILE, User
from promptpotter.infrastructure.store.wallet_store import (
    API_KEYS_FILE,
    CHARGES_FILE,
    GRANT_FILE,
    ChargeRecord,
    CouponGrant,
    HeldKey,
)

# By name in each account's directory; a `.jsonl` file holds one per line.
ACCOUNT_SURVIVORS: dict[str, TypeAdapter[Any]] = {
    USER_FILE: TypeAdapter(User),
    GRANT_FILE: TypeAdapter(CouponGrant),
    API_KEYS_FILE: TypeAdapter(dict[str, HeldKey]),
    CHARGES_FILE: TypeAdapter(ChargeRecord),
}

_IDENTITY = IdentityPaths(root=Path(IDENTITY_DIR))

# Globs under the data root.
SURVIVORS: dict[str, TypeAdapter[Any]] = {
    **{
        PurePosixPath(PROJECTS_DIR, "*", name).as_posix(): shape
        for name, shape in ACCOUNT_SURVIVORS.items()
    },
    _IDENTITY.default_claim_marker.as_posix(): TypeAdapter(DefaultClaim),
}


class Unloadable(NamedTuple):
    path: Path
    why: str


class SurvivorCheck(NamedTuple):
    held: int
    unloadable: tuple[Unloadable, ...]


def _load(path: Path, shape: TypeAdapter[Any]) -> None:
    text = path.read_text(encoding="utf-8")
    for one in text.splitlines() if path.suffix == ".jsonl" else (text,):
        shape.validate_json(one)


def check_survivors(data_root: Path) -> SurvivorCheck:
    held = 0
    unloadable: list[Unloadable] = []
    for where, shape in SURVIVORS.items():
        for path in sorted(data_root.glob(where)):
            try:
                _load(path, shape)
            except (OSError, ValueError) as exc:
                why = " ".join(f"{type(exc).__name__}: {exc}".split())
                unloadable.append(Unloadable(path, why))
            else:
                held += 1
    return SurvivorCheck(held, tuple(unloadable))


def survivor_schemas() -> dict[str, Any]:
    return {where: shape.json_schema() for where, shape in SURVIVORS.items()}
