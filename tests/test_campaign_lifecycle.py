"""Campaign lifecycle primitive — owner binding at mint, lifecycle filter at
the store gateway, soft-mark transitions (archive/delete/unarchive),
cross-user invisibility. One bundled test per the tests charter (one
canonical case per contract). Operator-facing shape:
``docs/operations/persistence-and-state.md`` § Beta hosting state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from promptpotter.domain.campaign import Campaign
from promptpotter.domain.identity import TenantId, UserId
from promptpotter.infrastructure.store import build_stores
from promptpotter.shared.identity import IdentityContext


def _mint(
    store_campaigns: object, *, campaign_id: str, owner: str, lifecycle: str = "active"
) -> None:
    now = datetime.now(UTC).isoformat()
    store_campaigns.create_campaign(  # type: ignore[attr-defined]
        Campaign(
            campaign_id=campaign_id,
            dataset_name="aime",
            created_at=now,
            root_cycle_id="cycle_deadbeef0001",
            owner_user_id=owner,
            lifecycle_status=lifecycle,  # type: ignore[arg-type]
            lifecycle_changed_at=now,
        )
    )


def test_campaign_lifecycle_contract(tmp_path: Path) -> None:
    """Owner-binding + lifecycle filter + soft-mark + cross-user invisibility."""
    alice = IdentityContext(user_id=UserId("alice"), tenant_id=TenantId("alice"))
    bob = IdentityContext(user_id=UserId("bob"), tenant_id=TenantId("bob"))

    alice_store = build_stores(alice, projects_root=tmp_path)
    bob_store = build_stores(bob, projects_root=tmp_path)

    # Mint two for alice (one active, one archived) + one for bob.
    _mint(alice_store.campaigns, campaign_id="aime__alice1", owner="alice")
    _mint(alice_store.campaigns, campaign_id="aime__alice2", owner="alice", lifecycle="archived")
    _mint(bob_store.campaigns, campaign_id="aime__bob1", owner="bob")

    # Default filter (lifecycle="active", owner-scoped) — alice sees only her active one.
    alice_active = alice_store.campaigns.list_campaigns(lifecycle="active", owner_user_id="alice")
    assert {c.campaign_id for c in alice_active} == {"aime__alice1"}

    # lifecycle="archived" surfaces the hidden one.
    alice_archived = alice_store.campaigns.list_campaigns(
        lifecycle="archived", owner_user_id="alice"
    )
    assert {c.campaign_id for c in alice_archived} == {"aime__alice2"}

    # lifecycle="all" surfaces both — soft-mark, not delete.
    alice_all = alice_store.campaigns.list_campaigns(lifecycle="all", owner_user_id="alice")
    assert {c.campaign_id for c in alice_all} == {"aime__alice1", "aime__alice2"}

    # Cross-user invisibility — alice's owner filter never returns bob's campaign,
    # even though bob's lives under his own tenant_id partition.
    leaked = [c for c in alice_all if c.owner_user_id != "alice"]
    assert not leaked, f"cross-user leak: {leaked}"

    # Soft-mark transition — archive flips lifecycle_status + bumps changed_at.
    before_at = alice_store.campaigns.load_campaign("aime__alice1").lifecycle_changed_at  # type: ignore[union-attr]
    alice_store.campaigns.mark_campaign_lifecycle(
        "aime__alice1",
        lifecycle_status="archived",
        lifecycle_changed_at=datetime.now(UTC).isoformat(),
        lifecycle_reason="cluttering sidebar",
    )
    after = alice_store.campaigns.load_campaign("aime__alice1")
    assert after is not None
    assert after.lifecycle_status == "archived"
    assert after.lifecycle_reason == "cluttering sidebar"
    assert after.lifecycle_changed_at != before_at

    # Unarchive — same writer, status flips back to "active".
    alice_store.campaigns.mark_campaign_lifecycle(
        "aime__alice1",
        lifecycle_status="active",
        lifecycle_changed_at=datetime.now(UTC).isoformat(),
    )
    restored = alice_store.campaigns.load_campaign("aime__alice1")
    assert restored is not None
    assert restored.lifecycle_status == "active"

    # Physical artifacts survive — campaign.json + cycle dir are still on disk
    # after any number of soft-marks. (Soft-mark ≠ delete; measurements survive.)
    alice_store.campaigns.mark_campaign_lifecycle(
        "aime__alice1",
        lifecycle_status="deleted",
        lifecycle_changed_at=datetime.now(UTC).isoformat(),
    )
    manifest = tmp_path / "alice" / "campaigns" / "aime__alice1" / "campaign.json"
    assert manifest.exists(), "soft-deletion must not remove campaign.json"
