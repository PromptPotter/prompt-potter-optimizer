# infrastructure/identity/ — sign-in

**The access model is owned by [ADR-0002](../../../docs/decisions.md) § 0002 and [`docs/operations/access-model.md`](../../../docs/operations/access-model.md)**; this package builds the `IdentityContext` `build_stores` takes, and the capability vocabulary stays in `shared/identity.py`. **The first web sign-in RENAMES `.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`** (`default_claim.py`).
