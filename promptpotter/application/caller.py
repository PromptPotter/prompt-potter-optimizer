"""Who a door's call runs as."""

from __future__ import annotations

from dataclasses import dataclass

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.infrastructure.identity.accounts import IdentityBundle, still_carried
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.identity import Carrier


@dataclass(frozen=True, slots=True)
class Caller:
    """Stores built from ONE `IdentityContext`, the job registry a server process holds (attached
    from the stores where the door holds none, the dispatcher's rule), and the sign-in the door's
    install configured (none at a terminal), which re-reads the carrier its identity came by on each
    tick of a stream (none where nothing was presented: the terminal, a server whose auth is off)."""

    stores: Stores
    attached: JobRegistry | None = None
    signing: IdentityBundle | None = None
    carrier: Carrier | None = None

    @property
    def providers(self) -> tuple[str, ...]:
        return () if self.signing is None else tuple(self.signing.config.configured)

    def still_signed(self) -> bool:
        """Whether the identity it was opened as still stands (`accounts.py::still_carried`)."""
        return (
            self.signing is None
            or self.carrier is None
            or still_carried(self.stores.identity, self.carrier, self.signing)
        )

    @classmethod
    def holding(cls, stores: Stores) -> Caller:
        """A process of its own: it holds the registry its launches' watchers ride for its life."""
        return cls(stores, JobRegistry.attach(stores.jobs_dir))

    @property
    def jobs(self) -> JobRegistry:
        return self.attached or JobRegistry.attach(self.stores.jobs_dir)
