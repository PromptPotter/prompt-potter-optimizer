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


def default_identity_paths() -> IdentityPaths:
    return IdentityPaths(root=user_data_root() / "identity")


__all__ = ["IdentityPaths", "default_identity_paths"]
