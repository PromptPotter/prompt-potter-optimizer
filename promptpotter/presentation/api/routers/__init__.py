"""FastAPI routers grouped by resource family.

* ``backends`` — backend registration + native-format sync + pipeline discovery
* ``campaigns`` — campaign registry + per-cycle live reads
* ``active`` — active-session pointer + read-only cycle/optimizer/registry reads
* ``datasets`` — dataset preview + ingest for New-Job view
* ``commands`` — the ``POST /commands/{kind}`` Control-remote highway (sole mutating seam)
* ``origins`` — campaign-origin reads
* ``verify`` — diagnostic-run verify
* ``auth`` — OIDC auth
"""
