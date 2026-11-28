"""An account's API tokens (`IdentityPaths.tokens`): each kept as its plaintext's SHA-256 alone, so
the file names who a bearer is and never holds one. A revoke stamps the row; nothing writes per use."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import ValidationError, field_serializer, field_validator

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import write_json, write_lock
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, FileKind, NextAct
from promptpotter.shared.identity import DELEGABLE_CAPABILITIES

TOKEN_PREFIX = "pp_"
_TOKEN_BYTES = 32
# `secrets.token_urlsafe(32)` spells 32 bytes in 43 characters.
TOKEN_CHARS = len(TOKEN_PREFIX) + 43
# An account's live tokens, and the revoked rows it keeps: the file every bearer request parses
# stays bounded by the accounts, never by how often one mints.
MAX_LIVE_TOKENS = 10
_KEPT_REVOKED = 10


class ApiToken(FrozenModel):
    id: str
    user_id: str
    name: str
    scopes: frozenset[str]
    created_at: str
    revoked_at: str | None = None

    @field_validator("scopes")
    @classmethod
    def _delegable(cls, scopes: frozenset[str]) -> frozenset[str]:
        """A row naming an owner-only or a blank scope never loads, so no bearer reads through it."""
        if not scopes <= DELEGABLE_CAPABILITIES:
            raise ValueError(f"scopes {sorted(scopes - DELEGABLE_CAPABILITIES)} are not delegable")
        return scopes

    @field_serializer("scopes")
    def serialize_scopes(self, scopes: frozenset[str]) -> list[str]:
        return sorted(scopes)


class TokensFile(FrozenModel):
    """Keyed by the plaintext's SHA-256 hex, each row unread: a row loads alone (`_tokens`)."""

    tokens: dict[str, dict[str, Any]]


def _digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


class _Held(NamedTuple):
    tokens: dict[str, ApiToken]
    # The rows that do not load, as their bytes stood: each its own bearer's 401, kept by a rewrite.
    torn: dict[str, dict[str, Any]]


def _tokens(path: Path) -> _Held:
    """Absent is no tokens; an envelope that does not validate raises, so no bearer reads through
    it; a row that does not is unknown, and only its bearer's."""
    rows = read_file(path, TokensFile.model_validate_json, FileKind.STORED_CONFIG)
    held = _Held({}, {})
    for digest, row in ({} if rows is None else rows.tokens).items():
        try:
            held.tokens[digest] = ApiToken.model_validate(row)
        except ValidationError:
            held.torn[digest] = row
    return held


def _write(path: Path, held: _Held) -> None:
    """Each account's newest `_KEPT_REVOKED` revoked rows kept, older ones dropped: a dropped
    token reads unknown."""
    tokens = held.tokens
    revoked = sorted(
        (t for t in tokens.items() if t[1].revoked_at is not None),
        key=lambda held: held[1].revoked_at or "",
        reverse=True,
    )
    kept: dict[str, int] = {}
    for digest, token in revoked:
        kept[token.user_id] = kept.get(token.user_id, 0) + 1
        if kept[token.user_id] > _KEPT_REVOKED:
            del tokens[digest]
    loaded = {digest: token.model_dump(mode="json") for digest, token in tokens.items()}
    write_json(path, {"tokens": {**held.torn, **loaded}})


def account_tokens(path: Path, user_id: str) -> list[ApiToken]:
    return sorted(
        (token for token in _tokens(path).tokens.values() if token.user_id == user_id),
        key=lambda token: token.created_at,
    )


def mint_token(
    path: Path, *, user_id: str, name: str, scopes: frozenset[str]
) -> tuple[str, ApiToken]:
    """The plaintext, returned here once and never again, beside the row its hash files; refused
    past `MAX_LIVE_TOKENS` live ones of *user_id*'s."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    token = ApiToken(
        id=secrets.token_hex(8), user_id=user_id, name=name, scopes=scopes, created_at=utcnow_iso()
    )
    with write_lock(path):
        held = _tokens(path)
        live = sum(t.user_id == user_id and t.revoked_at is None for t in held.tokens.values())
        if live >= MAX_LIVE_TOKENS:
            raise ConflictError(
                f"Your account holds {live} live tokens, the most it may.",
                code="tokens_full",
                next_acts=(
                    NextAct(
                        kind="revoke-api-token",
                        field="token_id",
                        value="the id of a live token you no longer use",
                    ),
                ),
            )
        _write(path, held._replace(tokens={**held.tokens, _digest(plaintext): token}))
    return plaintext, token


def revoke_token(path: Path, *, user_id: str, token_id: str) -> bool:
    """Whether a live token of *user_id*'s was revoked; another account's id reads as none."""
    with write_lock(path):
        held = _tokens(path)
        found = next(
            (
                (digest, token)
                for digest, token in held.tokens.items()
                if token.id == token_id and token.user_id == user_id and token.revoked_at is None
            ),
            None,
        )
        if found is None:
            return False
        digest, token = found
        revoked = token.model_copy(update={"revoked_at": utcnow_iso()})
        _write(path, held._replace(tokens={**held.tokens, digest: revoked}))
    return True


def drop_account(path: Path, user_id: str) -> int:
    """How many rows of *user_id*'s went, live and revoked: a deleted account's bearer reads unknown."""
    with write_lock(path):
        held = _tokens(path)
        kept = _Held(
            {digest: t for digest, t in held.tokens.items() if t.user_id != user_id},
            {digest: row for digest, row in held.torn.items() if row.get("user_id") != user_id},
        )
        gone = len(held.tokens) + len(held.torn) - len(kept.tokens) - len(kept.torn)
        if gone:
            _write(path, kept)
    return gone


def token_of(path: Path, plaintext: str) -> ApiToken | None:
    return _tokens(path).tokens.get(_digest(plaintext))
