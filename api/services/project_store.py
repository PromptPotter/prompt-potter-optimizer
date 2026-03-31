"""
File-based project store — composite of focused store modules.

All callers import ``ProjectStore`` from here and access domain stores
as public attributes: ``store.backends``, ``store.dataset_runs``, etc.

Layout on disk::

    .promptpotter/projects/
      {backend_id}/
        backend.json
        sync/
          experiments.json
          experiments/{experiment_id}.json
        executions/{execution_id}.json
        datasets/train.json
        datasets/test_processes.json
        datasets/test_material.json
        dataset_runs/{run_id}.json
        dataset_runs.json
        smart_search_plans/{plan_id}.json
        campaigns/{campaign_id}.json
        campaigns/{campaign_id}/trial_NNNN.json
"""

from pathlib import Path

from api.services.stores.backend_store import BackendStore
from api.services.stores.campaign_store import CampaignStore
from api.services.stores.dataset_run_store import DatasetRunStore
from api.services.stores.intermediate_cache import IntermediateCache
from api.services.stores.plan_store import PlanStore

BASE_DIR = Path(".promptpotter") / "projects"


class ProjectStore:
    """Composite store — access domain stores as public attributes."""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else BASE_DIR
        self.backends = BackendStore(self.base_dir)
        self.campaigns = CampaignStore(self.base_dir)
        self.dataset_runs = DatasetRunStore(self.base_dir)
        self.smart_search = PlanStore(self.base_dir)
        self.intermediate_cache = IntermediateCache(self.base_dir)
