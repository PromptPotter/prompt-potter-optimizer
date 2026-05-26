"""Repo-local paths for OIDC config + sessions + claim ledger.

The whole identity surface (provider config, allowlist, session store,
default-claim marker) lives at `.promptpotter/identity/` next to the
existing `.promptpotter/projects/` tree. Single git-ignored data dir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT


@dataclass(frozen=True)
class IdentityPaths:
    """Path bundle rooted at `.promptpotter/identity/`."""

    root: Path

    @property
    def provider_config(self) -> Path:
        return self.root / "oidc.json"

    @property
    def allowlist(self) -> Path:
        return self.root / "allowlist.json"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def default_claim_marker(self) -> Path:
        return self.root / "default_claimed.json"


def default_identity_paths() -> IdentityPaths:
    """Identity data dir sibling of the default `projects/` root."""
    return IdentityPaths(root=DEFAULT_PROJECTS_ROOT.parent / "identity")


__all__ = ["IdentityPaths", "default_identity_paths"]
