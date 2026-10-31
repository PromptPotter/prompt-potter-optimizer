from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from promptpotter.config.paths import user_data_root
from promptpotter.domain.strict_model import FrozenModel


@dataclass(frozen=True)
class IdentityPaths:
    root: Path

    @property
    def provider_config(self) -> Path:
        return self.root / "oidc.json"

    @property
    def blocklist(self) -> Path:
        return self.root / "blocklist.json"

    @property
    def blocklist_audit(self) -> Path:
        return self.root / "blocklist_audit.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def default_claim_marker(self) -> Path:
        return self.root / "default_claimed.json"

    @property
    def grants(self) -> Path:
        return self.root / "grants.json"

    @property
    def grants_audit(self) -> Path:
        return self.root / "grants_audit.jsonl"


IDENTITY_DIR = "identity"


class DefaultClaim(FrozenModel):
    """``default_claim_marker``: the host's account, written at its first sign-in; `renamed` is whether `default` became it."""

    user_id: str = Field(min_length=1)
    claimed_at: str
    renamed: bool


def default_identity_paths() -> IdentityPaths:
    return IdentityPaths(root=user_data_root() / IDENTITY_DIR)
