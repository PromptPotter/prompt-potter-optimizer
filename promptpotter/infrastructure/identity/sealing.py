"""The box key, and the seal an account's provider keys wear at rest (`wallet_store.py::TenantApiKeyStore`).

The key is 32 random bytes at `IdentityPaths.box_key`, made by the first seal on an install that
holds none, so a fresh clone, the desk and the offline world ask nothing. A seal is encrypt-then-MAC
with HMAC-SHA256 as both the counter-mode keystream and the tag, under subkeys derived from the box
key: the stdlib alone, since core reads a key on every send and carries no cipher package
(decisions § 0006). A wrong key or a changed byte refuses; it never reads as another secret."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

from promptpotter.infrastructure.store.io import read_bytes_optional, write_bytes, write_lock

_KEY_BYTES = 32
_NONCE_BYTES = 16
_TAG_BYTES = hashlib.sha256().digest_size


class SealError(ValueError):
    """The box key is gone or not this seal's, or the sealed bytes changed."""


def _held(path: Path) -> bytes | None:
    key = read_bytes_optional(path)
    if key is not None and len(key) != _KEY_BYTES:
        raise SealError(f"the box key at {path} is not {_KEY_BYTES} bytes")
    return key


def fresh_box_key() -> bytes:
    return secrets.token_bytes(_KEY_BYTES)


def box_key(path: Path) -> bytes:
    """The install's key, made under its write lock where none is: two first seals make one."""
    if (key := _held(path)) is not None:
        return key
    with write_lock(path):
        if (key := _held(path)) is None:
            key = fresh_box_key()
            write_bytes(path, key)
    return key


def _sub(key: bytes, purpose: bytes) -> bytes:
    return hmac.digest(key, purpose, "sha256")


def _stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    enc = _sub(key, b"seal:stream")
    blocks = range(0, len(data), _TAG_BYTES)
    pad = b"".join(hmac.digest(enc, nonce + i.to_bytes(8, "big"), "sha256") for i in blocks)
    return bytes(a ^ b for a, b in zip(data, pad, strict=False))


def _tag(key: bytes, bound: str, nonce: bytes, body: bytes) -> bytes:
    said = bound.encode()
    framed = len(said).to_bytes(4, "big") + said + nonce + body
    return hmac.digest(_sub(key, b"seal:tag"), framed, "sha256")


def seal(key: bytes, plaintext: str, *, bound: str) -> str:
    """*plaintext* sealed under *key*, opened only by the same *bound* (what slot it fills)."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    body = _stream(key, nonce, plaintext.encode())
    return base64.urlsafe_b64encode(nonce + body + _tag(key, bound, nonce, body)).decode()


def unseal(key: bytes | None, sealed: str, *, bound: str) -> str:
    if key is None:
        raise SealError("no box key is held, so no sealed key opens")
    raw = base64.urlsafe_b64decode(sealed.encode())
    if len(raw) < _NONCE_BYTES + _TAG_BYTES:
        raise SealError("the seal is shorter than its nonce and tag")
    nonce, body, tag = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:-_TAG_BYTES], raw[-_TAG_BYTES:]
    if not hmac.compare_digest(tag, _tag(key, bound, nonce, body)):
        raise SealError("the seal does not open under this box key")
    return _stream(key, nonce, body).decode()


def opened_key(path: Path) -> bytes | None:
    """The key a read opens with: never made, so a lost key refuses instead of sealing anew."""
    return _held(path)
