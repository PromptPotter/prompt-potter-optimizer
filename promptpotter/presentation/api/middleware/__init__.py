"""API-seam middleware — actuators between FastAPI routes and the canonical ledger.

``CommandDispatcher`` is the sole writer of ``CommandRecord`` at the API
boundary. Cycle-scoped commands ride the target cycle's ledger; workspace-
scoped lifecycle commands (archive / delete / unarchive campaign) ride the
campaign's root cycle ledger and apply inline.

``oidc.install_oidc_middleware`` is the Stage-1 identity ingress — populates
``request.state.identity_ctx`` from the opaque session cookie. Per ADR-0002
no-drift gate #2, no JWT type ever appears past this boundary.
"""
