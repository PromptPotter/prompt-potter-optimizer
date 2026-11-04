# application/datasets/ — loaders, the prompt store, the check-in draft

**Every ingress edits an origin through an `EditDraftPatch`, and the rules applying it live here (`draft_patch.py`), never in `api/routers/`.** **A pure projection off a draft lives in `draft_campaign.py`** — `draft_build.py` the served ones, `origin_readiness.py` the gate and write-back — because `pipeline_resolve` reads a draft and `jobs/launcher/` calls `pipeline_resolve`: a projection parked in the launcher closes an import cycle.

**A draft is kept only once its response is built** (`ingest.py::_kept`, the `edit-draft-campaign` and `resolve-origin` handlers): a build that raises keeps nothing.
