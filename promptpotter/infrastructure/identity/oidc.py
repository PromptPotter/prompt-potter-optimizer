"""Sign-in: the provider config, the ID-token verifier and its key cache, each provider's client, the
server-side session.

The PROVIDER checks ``redirect_uri`` against its app page; we verify only the inbound ``state``.
GitHub is OAuth 2.0, not OIDC: no ID token to verify, and a synthetic issuer keeps `(iss, sub)`
provider-distinct, so its flow's `nonce` binds nothing; the `state` the callback checks is the
flow's one bind. The session cookie carries the session id only — no JWT, no signed envelope
(decisions.md § 0002)."""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol, override
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPublicKey,
    RSAPublicNumbers,
)
from pydantic import Field, ValidationError

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    unlink_robust,
    validate_path_component,
    write_json,
)
from promptpotter.shared.errors import FileKind, FileUnreadableError, validation_reason
from promptpotter.shared.tls import tls_context

_NonEmpty = Annotated[str, Field(min_length=1)]


class OIDCProviderConfig(FrozenModel):
    client_id: _NonEmpty
    client_secret: _NonEmpty
    redirect_uri: _NonEmpty
    issuer: _NonEmpty | None = None
    authorize_url: _NonEmpty | None = None
    token_url: _NonEmpty | None = None
    jwks_url: _NonEmpty | None = None


class ProviderConfigBundle(FrozenModel):
    google: OIDCProviderConfig | None = None
    github: OIDCProviderConfig | None = None

    @property
    def configured(self) -> tuple[str, ...]:
        return tuple(name for name in SUPPORTED_PROVIDERS if getattr(self, name) is not None)


SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(ProviderConfigBundle.model_fields)


@dataclass(frozen=True)
class ProviderIdentity:
    issuer: str
    subject: str
    email: str | None
    provider: str


class SignInClient(Protocol):
    """One provider's half of a sign-in: `nonce` is the flow's, bound into an ID token where the provider issues one."""

    def authorize_url(self, *, state: str, nonce: str) -> str: ...

    async def exchange_code(self, *, code: str, nonce: str) -> ProviderIdentity: ...


_ERROR_CODE = re.compile(r"[a-z_]{1,64}")


class CodeExchangeError(RuntimeError):
    """A refused code exchange: the provider's error CODE travels, never its body."""

    def __init__(self, provider: str, error: str) -> None:
        super().__init__(f"{provider} code exchange refused: {error}")
        self.provider = provider
        self.error = error

    @classmethod
    def refused(cls, provider: str, status: int, body: bytes) -> CodeExchangeError:
        try:
            said = json.loads(body).get("error")
        except (ValueError, AttributeError):
            said = None
        code = said if isinstance(said, str) and _ERROR_CODE.fullmatch(said) else f"http_{status}"
        return cls(provider, code)


def load_provider_config(path: Path) -> ProviderConfigBundle:
    """Refused as the `FileKind.STORED_CONFIG` it is, each failing field by its path and rule:
    a value (a client secret) never leaves."""
    raw = read_bytes_optional(path)
    if raw is None:
        return ProviderConfigBundle()
    try:
        return ProviderConfigBundle.model_validate_json(raw)
    except ValidationError as exc:
        raise FileUnreadableError(
            FileKind.STORED_CONFIG, path=str(path), reason=validation_reason(exc, shown=3)
        ) from exc


logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL_S = 60 * 60


def _b64url_decode_int(value: str) -> int:
    padding_chars = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding_chars)
    return int.from_bytes(raw, "big")


def _jwk_to_rsa_public_key(jwk: dict[str, Any]) -> RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise ValueError(f"unsupported JWK kty: {jwk.get('kty')!r} (only RSA supported)")
    n = _b64url_decode_int(str(jwk["n"]))
    e = _b64url_decode_int(str(jwk["e"]))
    return RSAPublicNumbers(e=e, n=n).public_key()


@dataclass
class _JWKSEntry:
    keys: dict[str, RSAPublicKey]
    fetched_at: float


class JWKSCache:
    def __init__(self, ttl_s: float = _DEFAULT_CACHE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._entries: dict[str, _JWKSEntry] = {}
        self._lock = threading.Lock()

    async def get_key(self, jwks_uri: str, kid: str) -> RSAPublicKey:
        cached = self._lookup(jwks_uri, kid)
        if cached is not None:
            return cached
        await self._refresh(jwks_uri)
        cached = self._lookup(jwks_uri, kid)
        if cached is None:
            raise KeyError(f"kid {kid!r} not found in JWKS at {jwks_uri}")
        return cached

    def _lookup(self, jwks_uri: str, kid: str) -> RSAPublicKey | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(jwks_uri)
            if entry is None or (now - entry.fetched_at) > self._ttl_s:
                return None
            return entry.keys.get(kid)

    async def _refresh(self, jwks_uri: str) -> None:
        async with httpx.AsyncClient(timeout=10.0, verify=tls_context()) as client:
            response = await client.get(jwks_uri)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise ValueError(f"JWKS at {jwks_uri} is not a JSON object")
        raw_keys = body.get("keys")
        if not isinstance(raw_keys, list):
            raise ValueError(f"JWKS at {jwks_uri} missing 'keys' array")
        parsed: dict[str, RSAPublicKey] = {}
        for jwk in raw_keys:
            if not isinstance(jwk, dict) or "kid" not in jwk:
                continue
            try:
                parsed[str(jwk["kid"])] = _jwk_to_rsa_public_key(jwk)
            except (ValueError, KeyError) as exc:
                logger.warning("Skipping malformed JWK at %s: %s", jwks_uri, exc)
        with self._lock:
            self._entries[jwks_uri] = _JWKSEntry(keys=parsed, fetched_at=time.monotonic())


class IDTokenInvalidError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedIDToken:
    issuer: str
    subject: str
    email: str | None
    claims: dict[str, Any]


def _b64url_decode(value: str) -> bytes:
    padding_chars = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding_chars)


async def verify_id_token(
    id_token: str,
    *,
    expected_issuer: str,
    expected_audience: str,
    jwks_uri: str,
    jwks_cache: JWKSCache,
    leeway_s: int = 30,
) -> VerifiedIDToken:
    try:
        header_b64, payload_b64, signature_b64 = id_token.split(".")
    except ValueError as exc:
        raise IDTokenInvalidError(f"malformed JWT: expected 3 segments ({exc})") from exc

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise IDTokenInvalidError(f"malformed JWT body: {exc}") from exc

    if header.get("alg") != "RS256":
        raise IDTokenInvalidError(f"unsupported alg: {header.get('alg')!r} (require RS256)")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise IDTokenInvalidError("JWT header missing 'kid'")

    try:
        key = await jwks_cache.get_key(jwks_uri, kid)
    except KeyError as exc:
        raise IDTokenInvalidError(f"kid not in JWKS: {exc}") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode()
    try:
        key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise IDTokenInvalidError("signature verification failed") from exc

    iss = payload.get("iss")
    if iss != expected_issuer:
        raise IDTokenInvalidError(f"iss mismatch: got {iss!r}, expected {expected_issuer!r}")

    aud = payload.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if expected_audience not in audiences:
        raise IDTokenInvalidError(f"aud mismatch: {aud!r} does not include {expected_audience!r}")

    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int | float) or now > exp + leeway_s:
        raise IDTokenInvalidError("token expired")
    iat = payload.get("iat")
    if isinstance(iat, int | float) and iat > now + leeway_s:
        raise IDTokenInvalidError("iat in the future")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise IDTokenInvalidError("sub claim missing or empty")

    # An absent `email_verified` counts as UNVERIFIED; ``None`` rather than a raise, so no IdP lockout.
    email_verified = payload.get("email_verified")
    email_raw = payload.get("email")
    email = str(email_raw) if isinstance(email_raw, str) and email_verified is True else None

    return VerifiedIDToken(
        issuer=iss,
        subject=sub,
        email=email,
        claims=payload,
    )


GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


class GoogleProviderClient(SignInClient):
    def __init__(self, config: OIDCProviderConfig, jwks_cache: JWKSCache) -> None:
        self._config = config
        self._jwks = jwks_cache
        self._issuer = config.issuer or GOOGLE_ISSUER
        self._authorize_url = config.authorize_url or GOOGLE_AUTH_URL
        self._token_url = config.token_url or GOOGLE_TOKEN_URL
        self._jwks_url = config.jwks_url or GOOGLE_JWKS_URL

    @override
    def authorize_url(self, *, state: str, nonce: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{self._authorize_url}?{urlencode(params)}"

    @override
    async def exchange_code(self, *, code: str, nonce: str) -> ProviderIdentity:
        async with httpx.AsyncClient(timeout=15.0, verify=tls_context()) as client:
            response = await client.post(
                self._token_url,
                data={
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self._config.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise CodeExchangeError.refused("google", response.status_code, response.content)
        id_token_raw = response.json().get("id_token")
        if not isinstance(id_token_raw, str):
            raise CodeExchangeError("google", "missing_id_token")

        verified: VerifiedIDToken = await verify_id_token(
            id_token_raw,
            expected_issuer=self._issuer,
            expected_audience=self._config.client_id,
            jwks_uri=self._jwks_url,
            jwks_cache=self._jwks,
        )

        nonce_claim = verified.claims.get("nonce")
        if nonce_claim != nonce:
            raise IDTokenInvalidError(f"nonce mismatch: got {nonce_claim!r}, expected {nonce!r}")

        return ProviderIdentity(
            issuer=verified.issuer,
            subject=verified.subject,
            email=verified.email,
            provider="google",
        )


GITHUB_ISSUER = "https://github.com"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAIL_URL = "https://api.github.com/user/emails"


class GitHubProviderClient(SignInClient):
    def __init__(self, config: OIDCProviderConfig) -> None:
        self._config = config

    @override
    def authorize_url(self, *, state: str, nonce: str) -> str:
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": "read:user user:email",
            "state": state,
            "allow_signup": "true",
        }
        return f"{GITHUB_AUTH_URL}?{urlencode(params)}"

    @override
    async def exchange_code(self, *, code: str, nonce: str) -> ProviderIdentity:
        async with httpx.AsyncClient(timeout=15.0, verify=tls_context()) as client:
            token_response = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self._config.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            # GitHub refuses with a 200 whose body names the `error`, not with a 4xx.
            access_token = (
                token_response.json().get("access_token")
                if token_response.status_code == 200
                else None
            )
            if not isinstance(access_token, str) or not access_token:
                raise CodeExchangeError.refused(
                    "github", token_response.status_code, token_response.content
                )

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get(GITHUB_USER_URL, headers=auth_headers)
            if user_response.status_code != 200:
                raise CodeExchangeError("github", f"user_http_{user_response.status_code}")
            user_id = user_response.json().get("id")
            if not isinstance(user_id, int):
                raise CodeExchangeError("github", "user_without_id")

            # Only the verified list: `/user`'s `email` is unchecked, and the blocklist and host-admin claim match on it.
            email = None
            email_response = await client.get(GITHUB_EMAIL_URL, headers=auth_headers)
            if email_response.status_code == 200:
                email = _select_primary_verified_email(email_response.json())

        return ProviderIdentity(
            issuer=GITHUB_ISSUER,
            subject=str(user_id),
            email=email,
            provider="github",
        )


def _select_primary_verified_email(payload: object) -> str | None:
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if (
            isinstance(entry, dict)
            and entry.get("primary") is True
            and entry.get("verified") is True
            and isinstance(entry.get("email"), str)
        ):
            return str(entry["email"])
    for entry in payload:
        if (
            isinstance(entry, dict)
            and entry.get("verified") is True
            and isinstance(entry.get("email"), str)
        ):
            return str(entry["email"])
    return None


DEFAULT_SESSION_TTL_S = 60 * 60 * 24 * 7


class SessionData(FrozenModel):
    user_id: str
    tenant_id: str
    issuer: str
    subject: str
    email: str | None
    provider: str
    created_at: int
    expires_at: int

    def is_expired(self, now: int | None = None) -> bool:
        return (now if now is not None else int(time.time())) >= self.expires_at


class OIDCSessionStore:
    def __init__(self, sessions_dir: Path, ttl_s: int = DEFAULT_SESSION_TTL_S) -> None:
        self._dir = sessions_dir
        self._ttl_s = ttl_s

    def create(
        self,
        *,
        user_id: str,
        tenant_id: str,
        issuer: str,
        subject: str,
        email: str | None,
        provider: str,
    ) -> tuple[str, SessionData]:
        # Swept at write: a person who never returns leaves no email or user id on disk past the TTL.
        for held in self._dir.glob("*.json") if self._dir.is_dir() else ():
            _live(held)
        session_id = secrets.token_urlsafe(32)
        now = int(time.time())
        data = SessionData(
            user_id=user_id,
            tenant_id=tenant_id,
            issuer=issuer,
            subject=subject,
            email=email,
            provider=provider,
            created_at=now,
            expires_at=now + self._ttl_s,
        )
        write_json(self._dir / f"{session_id}.json", data.model_dump(mode="json"))
        return session_id, data

    def read(self, session_id: str) -> SessionData | None:
        path = self._path(session_id)
        return None if path is None else _live(path)

    def delete(self, session_id: str) -> None:
        if (path := self._path(session_id)) is not None:
            unlink_robust(path)

    def _path(self, session_id: str) -> Path | None:
        """``None`` for an id that names no file: the cookie is the caller's input."""
        try:
            return self._dir / f"{validate_path_component(session_id)}.json"
        except ValueError:
            return None


def _live(path: Path) -> SessionData | None:
    """A cache read: a session file is a login, so one this build cannot read or one past its TTL
    is deleted and reads as no session; the person signs in again."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    try:
        data: SessionData | None = SessionData.model_validate_json(raw)
    except ValidationError:
        data = None
    if data is None or data.is_expired():
        unlink_robust(path)
        return None
    return data
