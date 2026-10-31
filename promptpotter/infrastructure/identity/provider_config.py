"""The PROVIDER checks ``redirect_uri`` against its app page; we verify only the inbound ``state``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import Field, ValidationError

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import read_bytes_optional
from promptpotter.shared.errors import validation_reason

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


class OIDCConfigError(ValueError):
    pass


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
    raw = read_bytes_optional(path)
    if raw is None:
        return ProviderConfigBundle()
    try:
        return ProviderConfigBundle.model_validate_json(raw)
    except ValidationError as exc:
        raise OIDCConfigError(
            f"oidc.json does not validate: {validation_reason(exc, shown=3)}"
        ) from exc
