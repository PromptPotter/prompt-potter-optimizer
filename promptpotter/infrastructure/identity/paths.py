"""Repo-local paths for OIDC config, sessions and the claim ledger. The whole identity surface lives under one
git-ignored ``.promptpotter/identity/`` dir."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.config.paths import user_data_root


@dataclass(frozen=True)
class IdentityPaths:
    root: Path

    @property
    def provider_config(self) -> Path:
        return self.root / "oidc.json"

    @property
    def allowlist(self) -> Path:
        return self.root / "allowlist.json"

    @property
    def allowlist_audit(self) -> Path:
        return self.root / "allowlist_audit.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def default_claim_marker(self) -> Path:
        return self.root / "default_claimed.json"

    @property
    def grants(self) -> Path:
        """Sealed sub-principal grant store (ADR-0005) — the delegation authority
        file, in the same protected zone as the allowlist."""
        return self.root / "grants.json"

    @property
    def grants_audit(self) -> Path:
        return self.root / "grants_audit.jsonl"


def default_identity_paths() -> IdentityPaths:
    return IdentityPaths(root=user_data_root() / "identity")


__all__ = ["IdentityPaths", "default_identity_paths"]
