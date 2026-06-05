"""Dataset-scoped archive + the version-and-repoint that moves that scope.

The slice contract: writes stamp dataset_name into index + detail; reads
filter by it; default excludes unknown (pre-schema) entries; cache reuse
respects the scope. Bug being guarded: when one backend serves multiple
datasets, integer sample_id collides and a single unscoped read pools AIME
hits with JustLogic hits, corrupting Rasch + PoBB + L1 panels.

The migration contract (``dataset_replace.version_and_repoint``): replacing
the data under a name must NEVER overwrite — the old data + every dependent
campaign's results survive under ``{slug}-vN``, the pin (manifest + cycle
headers) and measurement stamps move with them, and a half-done migration is
recoverable. The data-safety crown jewel of the dataset bridge.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from promptpotter.application.datasets.dataset_replace import (
    recover_pending_replacements,
    version_and_repoint,
)
from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_observations,
)
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.identity import TenantId, UserId
from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.identity import IdentityContext


def _seed(
    archive: MeasurementArchive,
    *,
    run_id: str,
    dataset_name: str | None,
    sample_id: int = 0,
    hit: bool = True,
    content_hash: str | None = None,
) -> None:
    ch = content_hash or f"hash_{run_id}"
    data: dict[str, Any] = {
        "run_id": run_id,
        "name": run_id,
        "content_hash": ch,
        "prompt_fields_id": "pf_x",
        "item_count": 1,
        "scores": {"accuracy": 1.0 if hit else 0.0, "total": 1},
        "node_configs": [["llm_only", {"model": "X"}]],
        "pipeline_params": {"llm_only": {"model": "X"}},
        "created_at": "2026-05-19T00:00:00Z",
        "measurements": [
            {
                "sample_id": sample_id,
                "query": f"q_{dataset_name or 'unknown'}_{sample_id}",
                "ground_truth": "gt",
                "predicted": "p",
                "hit": hit,
                "fitness": 1.0 if hit else 0.0,
                "pipeline_data": {"terminated_at": "llm_only"},
            }
        ],
    }
    if dataset_name is not None:
        data["dataset_name"] = dataset_name
    archive.save("bk", run_id, data)


def test_save_writes_dataset_name(tmp_path: Path) -> None:
    """Phase 1 contract: the field lands in both the index summary and the per-run detail."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="r1", dataset_name="aime")

    index = json.loads((tmp_path / "archive" / "measurements_index.json").read_text())
    assert index["measurements"][0]["dataset_name"] == "aime"
    detail = json.loads((tmp_path / "archive" / "measurements" / "r1.json").read_text())
    assert detail["dataset_name"] == "aime"


def test_list_all_filters_by_dataset(tmp_path: Path) -> None:
    """Phase 2 contract: explicit dataset_name returns only matching entries."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_1", dataset_name="aime")
    _seed(archive, run_id="just_1", dataset_name="justlogic")

    aime_only = archive.list_all("bk", dataset_name="aime")
    assert [e["run_id"] for e in aime_only] == ["aime_1"]
    just_only = archive.list_all("bk", dataset_name="justlogic")
    assert [e["run_id"] for e in just_only] == ["just_1"]
    no_filter = archive.list_all("bk")
    assert {e["run_id"] for e in no_filter} == {"aime_1", "just_1"}


def test_unknown_entries_excluded_by_default(tmp_path: Path) -> None:
    """Pre-schema entries (v1, no dataset_name) drop out of cross-cycle views by default."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="old", dataset_name=None)  # v1-shape
    _seed(archive, run_id="new", dataset_name="aime")

    only_aime = archive.list_all("bk", dataset_name="aime")
    assert [e["run_id"] for e in only_aime] == ["new"]
    with_unknown = archive.list_all("bk", dataset_name="aime", include_unknown=True)
    assert {e["run_id"] for e in with_unknown} == {"new", "old"}


def test_archive_observations_dataset_scoped(tmp_path: Path) -> None:
    """build_archive_observations filters by dataset — colliding sample_ids stay isolated."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_5", dataset_name="aime", sample_id=14, hit=False)
    _seed(archive, run_id="just_5", dataset_name="justlogic", sample_id=14, hit=True)
    stores = SimpleNamespace(archive=archive)

    aime_obs = build_archive_observations(stores, "bk", dataset_name="aime")
    assert {(o.sample_id, o.hit) for o in aime_obs} == {(14, False)}
    just_obs = build_archive_observations(stores, "bk", dataset_name="justlogic")
    assert {(o.sample_id, o.hit) for o in just_obs} == {(14, True)}


def test_hit_cache_respects_dataset(tmp_path: Path) -> None:
    """load_reusable_results scopes by dataset — identical node-configs don't bleed across datasets."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_cached", dataset_name="aime", sample_id=14, hit=True)
    _seed(archive, run_id="just_fresh", dataset_name="justlogic", sample_id=14, hit=False)

    node_configs = [("llm_only", {"model": "X"})]
    aime_cache = archive.load_reusable_results("bk", node_configs, dataset_name="aime")
    just_cache = archive.load_reusable_results("bk", node_configs, dataset_name="justlogic")

    # Same sample_id, different dataset → different query texts → cached
    # results for one dataset are unreachable under the other's dataset_name.
    aime_queries = set(aime_cache.keys())
    just_queries = set(just_cache.keys())
    assert aime_queries.isdisjoint(just_queries)
    assert any("aime" in q for q in aime_queries)
    assert any("justlogic" in q for q in just_queries)


def _commit_fake_dataset(stores: Any, slug: str) -> Path:
    """Hand-write a minimal committed dataset (cache + self-referential campaign.json)."""
    ds_dir = stores.tenant_datasets.dataset_dir(slug)
    ds_dir.mkdir(parents=True)
    (ds_dir / "cache.json").write_text(
        json.dumps({"name": slug, "items": [{"id": 0, "query": "q", "ground_truth": "g"}]}),
        encoding="utf-8",
    )
    (ds_dir / "campaign.json").write_text(
        json.dumps({"campaign_config": {"dataset_name": slug}}), encoding="utf-8"
    )
    return ds_dir


def _mint_campaign(stores: Any, *, campaign_id: str, dataset_name: str, cycle_id: str) -> Path:
    """Mint a campaign pinned to *dataset_name* + a cycle index stamping it in the header."""
    now = datetime.now(UTC).isoformat()
    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=campaign_id,
            dataset_name=dataset_name,
            created_at=now,
            root_cycle_id=cycle_id,
            owner_user_id="nieena",
            lifecycle_status="active",
            lifecycle_changed_at=now,
        )
    )
    idx_path = stores.campaigns.cycle_dir(campaign_id, cycle_id) / "index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(
        json.dumps({"header": {"dataset_name": dataset_name, "backend_id": "bk"}}),
        encoding="utf-8",
    )
    return idx_path


def test_version_and_repoint_preserves_results(tmp_path: Path) -> None:
    """Replace = version-and-repoint: the old data + every dependent campaign's
    results survive under ``{slug}-v1``, never overwritten. The pin (manifest +
    cycle header + self-ref) and measurement stamps follow the data, and a
    half-done migration is recoverable from its marker."""
    ident = IdentityContext(user_id=UserId("nieena"), tenant_id=TenantId("nieena"))
    stores = build_stores(ident, projects_root=tmp_path)

    _commit_fake_dataset(stores, "emails")
    idx_path = _mint_campaign(
        stores, campaign_id="emails__c1", dataset_name="emails", cycle_id="cycle_aaaa0001"
    )
    _seed(stores.archive, run_id="r1", dataset_name="emails")

    result = version_and_repoint(stores=stores, slug="emails")

    # Data moved out of the way; the canonical name is free.
    assert not stores.tenant_datasets.dataset_dir("emails").exists()
    assert stores.tenant_datasets.dataset_dir("emails-v1").is_dir()
    # The pin followed the data — manifest, cycle header, and the dataset's own
    # self-reference all now resolve the bytes the campaign always ran on.
    assert stores.campaigns.load_campaign("emails__c1").dataset_name == "emails-v1"  # type: ignore[union-attr]
    assert json.loads(idx_path.read_text())["header"]["dataset_name"] == "emails-v1"
    moved_cc = json.loads(
        (stores.tenant_datasets.dataset_dir("emails-v1") / "campaign.json").read_text()
    )
    assert moved_cc["campaign_config"]["dataset_name"] == "emails-v1"
    # Measurements re-stamped — reachable under the new name, gone under the old.
    assert [e["run_id"] for e in stores.archive.list_all("bk", dataset_name="emails-v1")] == ["r1"]
    assert stores.archive.list_all("bk", dataset_name="emails") == []
    assert result.repointed_campaigns == 1
    assert result.restamped_measurements == 1

    # Crash recovery — simulate a replace that versioned the data + wrote a
    # marker but died before repointing. The next access heals it idempotently.
    _commit_fake_dataset(stores, "notes")
    _mint_campaign(stores, campaign_id="notes__c1", dataset_name="notes", cycle_id="cycle_bbbb0001")
    stores.tenant_datasets.version_dataset("notes", "notes-v1")  # data moved…
    mig_dir = stores.tenant_datasets.migrations_dir()
    mig_dir.mkdir(parents=True, exist_ok=True)
    (mig_dir / "crash.json").write_text(
        json.dumps({"id": "crash", "from": "notes", "to": "notes-v1", "status": "pending"}),
        encoding="utf-8",
    )  # …but the campaign still points at the now-missing 'notes'.

    recover_pending_replacements(stores=stores)

    assert stores.campaigns.load_campaign("notes__c1").dataset_name == "notes-v1"  # type: ignore[union-attr]
    assert json.loads((mig_dir / "crash.json").read_text())["status"] == "completed"
