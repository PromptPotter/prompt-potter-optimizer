"""HTTP read API — backend storage + campaign registry + per-cycle live reads.

Nothing is re-exported here — ``main.py`` imports each router from its own
module under ``routers/`` and mounts it at ``/api/v1``:

1. **Backend storage** (``backends_router``) — manages backend connections,
   syncs experiments from backends in their native format, and exposes
   pipeline discovery.

2. **Campaign registry + per-cycle live reads** (``campaigns_router``) —
   lists + views optimization campaigns (nested under
   ``/backends/{backend_id}/campaigns``) plus dashboard passthrough, log.md,
   ledger reads + filtered views (decisions, forks, files).

3. **Active-session + global reads** (``active_router``) — active session
   (``/sessions/active``), global cycle list (``/cycles``),
   ``/machine-status``, and the optimizer-pipeline / evaluators registry
   reads. Per-route tags since it spans several resources.

4. **Datasets** (``datasets_router``) — campaign-sourced dataset preview
   for the New Job view.

5. **Verify** (``verify_router``) — workspace-scope diagnostic-run records
   produced by ``cmd_verify``; feeds the Verify tab.

6. **Commands** (``commands_router``) — the ``POST /commands/{kind}``
   Control-remote highway; closed inbound command set declared in
   ``docs/specs/m12-api-openapi.yaml``.

7. **Origins** (``origins_router``) — campaign-origin reads; ``GET /origins``
   derived view over ``list_campaigns()`` deduped by ``root_content_hash``.

8. **Auth** (``auth_router``) — Stage-1 OIDC sign-in surface (Google +
   GitHub). ``/auth/login/{provider}``, ``/auth/callback/{provider}``,
   ``/auth/logout``, ``/auth/me``.
   Runs pre-auth; populates the opaque session cookie consumed by the
   OIDC middleware.

``campaigns`` is a package whose ``__init__`` imports its own route submodules
so their decorators run (see ``routers/campaigns/_router.py``); the rest are
plain modules.
"""
