"""Probe: the feed's `machine` version is counted per user_id, but its stats are the CALLER's view
(host flag, acting principal). A host and its delegate share a user_id and a store; with both
feeds open, each observation flips the held stats, so `n` moves on every tick with no write."""

import os
import tempfile

_HOME = tempfile.mkdtemp(prefix="pp-fv-probe-")
os.environ["PROMPTPOTTER_HOME"] = _HOME
os.environ["PROMPTPOTTER_ENV_FILE"] = os.path.join(_HOME, ".env")

from pathlib import Path  # noqa: E402

from promptpotter.application.cycle_reads import feed_versions  # noqa: E402
from promptpotter.application.jobs.registry import JobRegistry  # noqa: E402
from promptpotter.application.maintenance.landing import complete_registries  # noqa: E402
from promptpotter.config.paths import default_projects_root, user_data_root  # noqa: E402
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir  # noqa: E402
from promptpotter.domain.job import CampaignLaunch  # noqa: E402
from promptpotter.infrastructure.store.stores import build_stores  # noqa: E402
from promptpotter.shared.identity import IdentityContext, TenantId, UserId  # noqa: E402

complete_registries()
root = default_projects_root()
datasets = user_data_root().parent / "datasets"
host = IdentityContext(user_id=UserId("ada"), tenant_id=TenantId("ada"), host=True)
# A delegate of ada: ada's user_id and tenant (its store), its own principal, never the host.
delegate = IdentityContext(
    user_id=UserId("ada"), tenant_id=TenantId("ada"), claims={"principal": "sub-1"}
)
s_host = build_stores(host, projects_root=root, install_datasets_root=datasets)
s_del = build_stores(delegate, projects_root=root, install_datasets_root=datasets)
assert s_host.base_dir == s_del.base_dir, "guard: one store"

registry = JobRegistry(Path(_HOME) / "jobs", capacity=lambda _live: 1)
ws = WorkspaceDir(Path(_HOME) / "ws")
registry.create(
    user_id="ada",
    principal_id="ada",
    workspace=ws,
    hop=CycleHop(campaign_id="c1", cycle_id="r1"),
    holder=CampaignLaunch("ds1"),
)
# A launch ada queued: listed to the principal that filed it alone.
registry.request_slot(user_id="ada", principal_id="ada", workspace=ws, holder=CampaignLaunch("ds1"))

print("same caller twice (no write):")
for _ in range(2):
    print("  host    ", feed_versions(s_host, registry, "machine")["machine"])
print("host and delegate alternating, nothing written:")
for i in range(6):
    who, name = (s_host, "host    ") if i % 2 == 0 else (s_del, "delegate")
    print(" ", name, feed_versions(who, registry, "machine")["machine"])
