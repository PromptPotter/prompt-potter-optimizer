# infrastructure/identity/ — sign-in

**The access model is a constitution, not a layer note** — boundaries, capabilities and enforcement are owned by [`docs/adr/0002-identity-foundation.md`](../../../docs/adr/0002-identity-foundation.md) and [`docs/operations/access-model.md`](../../../docs/operations/access-model.md); this package builds the `IdentityContext` `build_stores` takes, and the capability vocabulary that reads it stays in `shared/identity.py`. **The first web sign-in RENAMES `.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`** (`migration.py`).
