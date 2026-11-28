"""An account's three wallet files: what the host grants it on the box's keys, the keys it holds,
and what its sends were charged.

None holds a balance: what the coupon has left is a fold of the charges
(`store/account_spend.py::account_bill`), never a counter beside them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, SecretStr

from promptpotter.config.paths import short_form
from promptpotter.domain.records.money import ChargeLine
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.identity import sealing
from promptpotter.infrastructure.store.io import append_line, unlink_robust, write_json, write_lock
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind, FileUnreadableError

GRANT_FILE = "grant.json"
API_KEYS_FILE = "api_keys.json"
CHARGES_FILE = "charges.jsonl"


class CouponGrant(FrozenModel):
    """The host's coupon for one account (`account.py::set_coupon`), replacing the box's default
    face in both units."""

    face: Budget = Field(
        description="The coupon's face per unit; an axis left unset is unbounded on the box's keys."
    )
    granted_at: str = Field(
        description="Where the coupon draws from: each charge at or after it counts against the "
        "face (`account_spend.py::account_bill`)."
    )


class GrantStore:
    """Absent is the box's default coupon (`settings.HOST_COUPON`); corrupt raises, never reads as it."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / GRANT_FILE

    def load(self) -> CouponGrant | None:
        return read_file(self.path, CouponGrant.model_validate_json, FileKind.STORED_CONFIG)

    def set(self, face: Budget, *, afresh: bool, opened_at: str) -> CouponGrant:
        """Read and replaced under its write lock. It keeps the window the grant it replaces
        draws from (from *opened_at*, the account's opening, where none stands); *afresh* starts
        it now, so nothing the account was charged before counts against *face*."""
        with write_lock(self.path):
            held = self.load()
            granted_at = utcnow_iso() if afresh else opened_at if held is None else held.granted_at
            grant = CouponGrant(face=face, granted_at=granted_at)
            write_json(self.path, grant.model_dump(mode="json"))
        return grant


class HeldKey(FrozenModel):
    secret: SecretStr
    added_at: str = Field(default_factory=utcnow_iso)

    @property
    def last4(self) -> str:
        return self.secret.get_secret_value()[-4:]


class SealedKey(FrozenModel):
    """A held key as it rests: its secret sealed under the box key, bound to its account, slot
    and date (`identity/sealing.py`)."""

    sealed: str
    added_at: str


class SealedKeys(FrozenModel):
    """`api_keys.json`: an account's held keys sealed, and a tag binding the whole map to it."""

    keys: dict[str, SealedKey]
    tag: str


def _mapped(tenant: str, keys: Mapping[str, SealedKey]) -> list[str]:
    return [tenant, *(f for p in sorted(keys) for f in (p, keys[p].added_at, keys[p].sealed))]


def sealed_keys(key: bytes, tenant: str, held: Mapping[str, HeldKey]) -> SealedKeys:
    keys = {
        p: SealedKey(
            sealed=sealing.seal(key, k.secret.get_secret_value(), bound=(tenant, p, k.added_at)),
            added_at=k.added_at,
        )
        for p, k in held.items()
    }
    return SealedKeys(keys=keys, tag=sealing.file_tag(key, _mapped(tenant, keys)))


def opened_keys(key: bytes, tenant: str, file: SealedKeys) -> dict[str, HeldKey]:
    sealing.check_file_tag(key, _mapped(tenant, file.keys), file.tag)
    return {
        p: HeldKey(
            secret=SecretStr(sealing.unseal(key, k.sealed, bound=(tenant, p, k.added_at))),
            added_at=k.added_at,
        )
        for p, k in file.keys.items()
    }


class TenantApiKeyStore:
    """One key per provider; read on every send's resolution, so a removal binds the next send.
    An account holding none has no file."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / API_KEYS_FILE
        self.tenant = account_dir.name

    @property
    def box_key(self) -> Path:
        return sealing.key_path(self.path.parents[2])

    def load(self) -> dict[str, HeldKey]:
        # Optional, not tolerant: a corrupt file, or one its box key does not open, read as "no
        # keys" would bill the host's.
        return read_file(self.path, self._opened, FileKind.SEALED_KEYS) or {}

    def _opened(self, raw: bytes) -> dict[str, HeldKey]:
        file = SealedKeys.model_validate_json(raw)
        if (key := sealing.held_key(self.box_key)) is None:
            raise sealing.SealError(f"no box key at {self.box_key}")
        return opened_keys(key, self.tenant, file)

    def put(self, provider: str, secret: SecretStr) -> HeldKey:
        held = HeldKey(secret=secret)
        with write_lock(self.path):
            self._write({**self.load(), provider: held})
        return held

    def _write(self, keys: Mapping[str, HeldKey]) -> None:
        if not keys:
            unlink_robust(self.path)
            return
        key = sealing.held_key(self.box_key) or self._first_key()
        write_json(self.path, sealed_keys(key, self.tenant, keys).model_dump(mode="json"))

    def _first_key(self) -> bytes:
        """A key is made only on an install no sealed file is on: else the key was lost."""
        if held := list(self.path.parent.parent.glob(f"*/{API_KEYS_FILE}")):
            reason = f"no box key, while {len(held)} account(s) hold keys sealed under one"
            raise FileUnreadableError(FileKind.SEALED_KEYS, path=str(self.box_key), reason=reason)
        return sealing.made_key(self.box_key)

    def remove(self, provider: str) -> bool:
        """Re-sealed without *provider*; a file no box key opens is deleted by removing any key it
        lists, since none of them opens and a map short of one could not be re-tagged."""
        with write_lock(self.path):
            try:
                keys = self.load()
            except FileUnreadableError:
                file = read_file(self.path, SealedKeys.model_validate_json, FileKind.SEALED_KEYS)
                if file is None or provider not in file.keys:
                    raise
                unlink_robust(self.path)
                return True
            if provider not in keys:
                return False
            self._write({p: k for p, k in keys.items() if p != provider})
            return True


class ChargeBook:
    """Written at the send by `spend_book.py::Admission`, so no delete and no reshape of run data reads a ledger to keep it."""

    def __init__(self, account_dir: Path) -> None:
        self.account_dir = account_dir
        self.path = account_dir / CHARGES_FILE

    def append(self, *lines: ChargeLine) -> None:
        """One fact's *lines* in one locked write, so no crash or other writer lands between them."""
        raw = "\n".join(line.model_dump_json() for line in lines).encode("utf-8")
        append_line(self.path, raw)

    def held_on(self, ledger: Path) -> str:
        return Path(os.path.relpath(short_form(ledger), short_form(self.account_dir))).as_posix()
