"""The box key, and the seal an account's provider keys wear at rest (`wallet_store.py::TenantApiKeyStore`).

The key is 32 random bytes kept OFF the data root, so a backup or a copied data root carries the
sealed keys without what opens them: at `settings.BOX_KEY_FILE` (on the box, the unit's systemd
credential), else a per-user config file; a path inside the data root refuses. Only the landing
makes it, or a first seal on an install holding no sealed file: a lost key refuses, never replaced.

A seal is encrypt-then-MAC with HMAC-SHA256 as both the counter-mode keystream and the tag, under
subkeys derived from the box key: the stdlib alone, since core reads a key on every send and carries
no cipher package (decisions § 0006). Each seal says its format and the box key's id, and its tag
binds the fields it is *bound* to (its account, slot and date), so a seal copied to another slot or
account refuses; `file_tag` binds a file's whole map, so a dropped or swapped entry refuses. Deleting
the whole file, or rolling it back to an older copy, needs state outside it to refuse."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from promptpotter.config.paths import short_form
from promptpotter.config.settings import settings
from promptpotter.infrastructure.store.io import read_bytes_optional, write_bytes, write_lock

_FORMAT = 1
_KEY_BYTES = 32
_ID_BYTES = 8
_NONCE_BYTES = 16
_TAG_BYTES = hashlib.sha256().digest_size
_HEAD = 1 + _ID_BYTES + _NONCE_BYTES


class SealError(ValueError):
    """The box key is gone, short or not this seal's, or the sealed bytes changed."""


def key_path(data_root: Path) -> Path:
    named = settings.BOX_KEY_FILE
    path = Path(short_form((Path(named).expanduser() if named else _desk_key()).resolve()))
    if path.is_relative_to(short_form(data_root)):
        raise SealError(f"BOX_KEY_FILE={path} is inside the data root, which a backup carries")
    return path


def _desk_key() -> Path:
    if sys.platform == "win32":
        base = (
            Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "PromptPotter"
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences" / "PromptPotter"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "promptpotter"
    return base / "box.key"


def _valid(key: bytes, where: object) -> bytes:
    if len(key) != _KEY_BYTES:
        raise SealError(f"the box key {where} is {len(key)} bytes, not {_KEY_BYTES}")
    return key


def held_key(path: Path) -> bytes | None:
    """The one opener of the key file: absent is ``None``, a key not 32 bytes refuses."""
    key = read_bytes_optional(path)
    return None if key is None else _valid(key, f"at {path}")


def made_key(path: Path) -> bytes:
    """The held key, else one made under its write lock (two first seals make one). Its caller
    owns the right to make one: the landing, or a seal on an install that holds no sealed file."""
    if (key := held_key(path)) is not None:
        return key
    with write_lock(path):
        if (key := held_key(path)) is None:
            key = secrets.token_bytes(_KEY_BYTES)
            write_bytes(path, key)
    return key


def _sub(key: bytes, purpose: bytes) -> bytes:
    return hmac.digest(key, purpose, "sha256")


def _key_id(key: bytes) -> bytes:
    return _sub(key, b"seal:id")[:_ID_BYTES]


def _framed(fields: Sequence[str]) -> bytes:
    said = [field.encode() for field in (str(_FORMAT), *fields)]
    return b"".join(len(one).to_bytes(4, "big") + one for one in said)


def _stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    enc = _sub(key, b"seal:stream")
    blocks = range(0, len(data), _TAG_BYTES)
    pad = b"".join(hmac.digest(enc, nonce + i.to_bytes(8, "big"), "sha256") for i in blocks)
    return bytes(a ^ b for a, b in zip(data, pad, strict=False))


def _tag(key: bytes, bound: Sequence[str], head: bytes, body: bytes) -> bytes:
    return hmac.digest(_sub(key, b"seal:tag"), _framed(bound) + head + body, "sha256")


def seal(key: bytes, plaintext: str, *, bound: Sequence[str]) -> str:
    """*plaintext* sealed under *key*, opened only by the same *bound* fields."""
    head = (
        _FORMAT.to_bytes(1, "big")
        + _key_id(_valid(key, "sealing"))
        + secrets.token_bytes(_NONCE_BYTES)
    )
    body = _stream(key, head[-_NONCE_BYTES:], plaintext.encode())
    return base64.urlsafe_b64encode(head + body + _tag(key, bound, head, body)).decode()


def unseal(key: bytes, sealed: str, *, bound: Sequence[str]) -> str:
    try:
        raw = base64.urlsafe_b64decode(sealed.encode())
    except binascii.Error as exc:
        raise SealError("the seal is not base64") from exc
    if len(raw) < _HEAD + _TAG_BYTES or raw[0] != _FORMAT:
        raise SealError(f"the seal is not format {_FORMAT}")
    head, body, tag = raw[:_HEAD], raw[_HEAD:-_TAG_BYTES], raw[-_TAG_BYTES:]
    if head[1 : 1 + _ID_BYTES] != _key_id(_valid(key, "opening")):
        raise SealError("sealed under another box key")
    if not hmac.compare_digest(tag, _tag(key, bound, head, body)):
        raise SealError("the seal does not open for this slot")
    return _stream(key, head[-_NONCE_BYTES:], body).decode()


def file_tag(key: bytes, fields: Sequence[str]) -> str:
    """What binds a sealed file's whole map: *fields* framed, under the box key."""
    tag = hmac.digest(_sub(_valid(key, "tagging"), b"seal:file"), _framed(fields), "sha256")
    return base64.urlsafe_b64encode(tag).decode()


def check_file_tag(key: bytes, fields: Sequence[str], tag: str) -> None:
    if not hmac.compare_digest(tag.encode(), file_tag(key, fields).encode()):
        raise SealError("the file's map is not the one sealed: an entry was dropped or swapped")
