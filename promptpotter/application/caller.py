"""Who a door's call runs as."""

from __future__ import annotations

from dataclasses import dataclass

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True, slots=True)
class Caller:
    """Stores built from ONE `IdentityContext`, the job registry a server process holds (attached
    from the stores where the door holds none, the dispatcher's rule), and the sign-in providers
    the door's install configured (none at a terminal)."""

    stores: Stores
    attached: JobRegistry | None = None
    providers: tuple[str, ...] = ()

    @classmethod
    def holding(cls, stores: Stores) -> Caller:
        """A process of its own: it holds the registry its launches' watchers ride for its life."""
        return cls(stores, JobRegistry.attach(stores.jobs_dir))

    @property
    def jobs(self) -> JobRegistry:
        return self.attached or JobRegistry.attach(self.stores.jobs_dir)
